# This file is part of arduino-cli.
#
# Copyright 2020 ARDUINO SA (http://www.arduino.cc/)
#
# This software is released under the GNU General Public License version 3,
# which covers the main part of arduino-cli.
# The terms of this license can be found at:
# https://www.gnu.org/licenses/gpl-3.0.en.html
#
# You can be released from the requirements of the above licenses by purchasing
# a commercial license. Buying such a license is mandatory if you want to modify or
# otherwise use the software for commercial activities involving the Arduino
# software without disclosing the source code of your own applications. To purchase
# a commercial license, send an email to license@arduino.cc.
import os
import platform
import signal
import shutil
import time
from pathlib import Path

import pytest
import simplejson as json
from invoke import Local
from invoke.context import Context
import tempfile
from filelock import FileLock

from .common import Board


@pytest.fixture(scope="function")
def data_dir(tmpdir_factory):
    """
    A tmp folder will be created before running
    each test and deleted at the end, this way all the
    tests work in isolation.
    """

    # it seems that paths generated by pytest's tmpdir_factory are too
    # long and may lead to arduino-cli compile failures due to the
    # combination of (some or all of) the following reasons:
    # 1) Windows not liking path >260 chars in len
    # 2) arm-gcc not fully optimizing long paths
    # 3) libraries requiring headers deep down the include path
    # for example:
    #
    #             from C:\Users\runneradmin\AppData\Local\Temp\pytest-of-runneradmin\pytest-0\A7\packages\arduino\hardware\mbed\1.1.4\cores\arduino/mbed/rtos/Thread.h:29, # noqa: E501
    #             from C:\Users\runneradmin\AppData\Local\Temp\pytest-of-runneradmin\pytest-0\A7\packages\arduino\hardware\mbed\1.1.4\cores\arduino/mbed/rtos/rtos.h:28, # noqa: E501
    #             from C:\Users\runneradmin\AppData\Local\Temp\pytest-of-runneradmin\pytest-0\A7\packages\arduino\hardware\mbed\1.1.4\cores\arduino/mbed/mbed.h:23, # noqa: E501
    #             from C:\Users\runneradmin\AppData\Local\Temp\pytest-of-runneradmin\pytest-0\A7\packages\arduino\hardware\mbed\1.1.4\cores\arduino/Arduino.h:32, # noqa: E501
    #             from C:\Users\RUNNER~1\AppData\Local\Temp\arduino-sketch-739B2B6DD21EB014317DA2A46062811B\sketch\magic_wand.ino.cpp:1: # noqa: E501
    # [error]c:\users\runneradmin\appdata\local\temp\pytest-of-runneradmin\pytest-0\a7\packages\arduino\tools\arm-none-eabi-gcc\7-2017q4\arm-none-eabi\include\c++\7.2.1\new:39:10: fatal error: bits/c++config.h: No such file or directory # noqa: E501
    #
    # due to the above on Windows we cut the tmp path straight to /tmp/xxxxxxxx
    if platform.system() == "Windows":
        with tempfile.TemporaryDirectory() as tmp:
            yield tmp
            shutil.rmtree(tmp, ignore_errors=True)
    else:
        data = tmpdir_factory.mktemp("ArduinoTest")
        yield str(data)
        shutil.rmtree(data, ignore_errors=True)


@pytest.fixture(scope="session")
def downloads_dir(tmpdir_factory, worker_id):
    """
    To save time and bandwidth, all the tests will access
    the same download cache folder.
    """
    download_dir = tmpdir_factory.mktemp("ArduinoTest", numbered=False)

    # This folders should be created only once per session, if we're running
    # tests in parallel using multiple processes we need to make sure this
    # this fixture is executed only once, thus the use of the lockfile
    if not worker_id == "master":
        lock = Path(download_dir / "lock")
        with FileLock(lock):
            if not lock.is_file():
                lock.touch()

    yield str(download_dir)
    shutil.rmtree(download_dir, ignore_errors=True)


@pytest.fixture(scope="function")
def working_dir(tmpdir_factory):
    """
    A tmp folder to work in
    will be created before running each test and deleted
    at the end, this way all the tests work in isolation.
    """
    work_dir = tmpdir_factory.mktemp("ArduinoTestWork")
    yield str(work_dir)
    shutil.rmtree(work_dir, ignore_errors=True)


@pytest.fixture(scope="function")
def run_command(pytestconfig, data_dir, downloads_dir, working_dir):
    """
    Provide a wrapper around invoke's `run` API so that every test
    will work in the same temporary folder.

    Useful reference:
        http://docs.pyinvoke.org/en/1.4/api/runners.html#invoke.runners.Result
    """

    cli_path = Path(pytestconfig.rootdir).parent / "arduino-cli"
    env = {
        "ARDUINO_DATA_DIR": data_dir,
        "ARDUINO_DOWNLOADS_DIR": downloads_dir,
        "ARDUINO_SKETCHBOOK_DIR": data_dir,
    }
    (Path(data_dir) / "packages").mkdir()

    def _run(cmd_string, custom_working_dir=None, custom_env=None):

        if not custom_working_dir:
            custom_working_dir = working_dir
        if not custom_env:
            custom_env = env
        cli_full_line = '"{}" {}'.format(cli_path, cmd_string)
        run_context = Context()
        # It might happen that we need to change directories between drives on Windows,
        # in that case the "/d" flag must be used otherwise directory wouldn't change
        cd_command = "cd"
        if platform.system() == "Windows":
            cd_command += " /d"
        # Context.cd() is not used since it doesn't work correctly on Windows.
        # It escapes spaces in the path using "\ " but it doesn't always work,
        # wrapping the path in quotation marks is the safest approach
        with run_context.prefix(f'{cd_command} "{custom_working_dir}"'):
            return run_context.run(cli_full_line, echo=False, hide=True, warn=True, env=custom_env, encoding="utf-8")

    return _run


@pytest.fixture(scope="function")
def daemon_runner(pytestconfig, data_dir, downloads_dir, working_dir):
    """
    Provide an invoke's `Local` object that has started the arduino-cli in daemon mode.
    This way is simple to start and kill the daemon when the test is finished
    via the kill() function

    Useful reference:
        http://docs.pyinvoke.org/en/1.4/api/runners.html#invoke.runners.Local
        http://docs.pyinvoke.org/en/1.4/api/runners.html
    """
    cli_full_line = str(Path(pytestconfig.rootdir).parent / "arduino-cli daemon")
    env = {
        "ARDUINO_DATA_DIR": data_dir,
        "ARDUINO_DOWNLOADS_DIR": downloads_dir,
        "ARDUINO_SKETCHBOOK_DIR": data_dir,
    }
    (Path(data_dir) / "packages").mkdir()
    run_context = Context()
    # It might happen that we need to change directories between drives on Windows,
    # in that case the "/d" flag must be used otherwise directory wouldn't change
    cd_command = "cd"
    if platform.system() == "Windows":
        cd_command += " /d"
    # Context.cd() is not used since it doesn't work correctly on Windows.
    # It escapes spaces in the path using "\ " but it doesn't always work,
    # wrapping the path in quotation marks is the safest approach
    run_context.prefix(f'{cd_command} "{working_dir}"')
    # Local Class is the implementation of a Runner abstract class
    runner = Local(run_context)
    runner.run(cli_full_line, echo=False, hide=True, warn=True, env=env, asynchronous=True)

    # we block here until the test function using this fixture has returned
    yield runner

    # Kill the runner's process as we finished our test (platform dependent)
    os_signal = signal.SIGTERM
    if platform.system() != "Windows":
        os_signal = signal.SIGKILL
    os.kill(runner.process.pid, os_signal)


@pytest.fixture(scope="function")
def detected_boards(run_command):
    """
    This fixture provides a list of all the boards attached to the host.
    This fixture will parse the JSON output of `arduino-cli board list --format json`
    to extract all the connected boards data.

    :returns a list `Board` objects.
    """
    assert run_command("core update-index")
    result = run_command("board list --format json")
    assert result.ok

    detected_boards = []
    for port in json.loads(result.stdout):
        for board in port.get("boards", []):
            fqbn = board.get("FQBN")
            package, architecture, _id = fqbn.split(":")
            detected_boards.append(
                Board(
                    address=port.get("address"),
                    fqbn=fqbn,
                    package=package,
                    architecture=architecture,
                    id=_id,
                    core="{}:{}".format(package, architecture),
                )
            )

    return detected_boards


@pytest.fixture(scope="function")
def copy_sketch(working_dir):
    def _copy(sketch_name):
        # Copies sketch to working directory for testing
        sketch_path = Path(__file__).parent / "testdata" / sketch_name
        test_sketch_path = Path(working_dir, sketch_name)
        shutil.copytree(sketch_path, test_sketch_path)
        return str(test_sketch_path)

    return _copy


@pytest.fixture(scope="function")
def wait_for_board(run_command):
    def _waiter(seconds=10):
        # Waits for the specified amount of second for a board to be visible.
        # This is necessary since it might happen that a board is not immediately
        # available after a test upload and subsequent tests might consequently fail.
        time_end = time.time() + seconds
        while time.time() < time_end:
            result = run_command("board list --format json")
            ports = json.loads(result.stdout)
            if len([p.get("boards", []) for p in ports]) > 0:
                break

    return _waiter
