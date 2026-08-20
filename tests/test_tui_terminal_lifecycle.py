import os
import select
import struct
import subprocess
import sys
import tempfile
import time
import unittest


@unittest.skipUnless(os.name == "posix", "PTY test requires POSIX")
class TestTUITerminalLifecycle(unittest.TestCase):
    def test_alternate_screen_and_restore(self):
        import fcntl
        import pty
        import termios

        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        with tempfile.TemporaryDirectory() as root:
            env = dict(os.environ, TERM="xterm-256color")
            process = None
            output = bytearray()
            saw_restore = False
            process = subprocess.Popen(
                [sys.executable, "-m", "kitt.cli.main", "--ui", "tui", "--no-history", "--no-animation", "--root", root],
                stdin=slave, stdout=slave, stderr=slave, cwd=os.path.dirname(os.path.dirname(__file__)), env=env,
                close_fds=True,
            )
            try:
                os.close(slave)
                slave = None
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and b"K.I.T.T." not in output:
                    ready, _, _ = select.select([master], [], [], 0.1)
                    if ready:
                        output.extend(os.read(master, 65536))
                os.write(master, b"\x10")  # palette
                time.sleep(0.1)
                os.write(master, b"\x1b")  # close overlay
                time.sleep(0.2)
                os.write(master, b"/quit\r")
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    ready, _, _ = select.select([master], [], [], 0.1)
                    if not ready:
                        continue
                    try:
                        output.extend(os.read(master, 65536))
                    except OSError:
                        break
                    if b"\x1b[?1049l" in output:
                        saw_restore = True
                        break
                self.assertTrue(saw_restore, "alternate screen restore sequence not emitted")
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            finally:
                if process and process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                if slave is not None:
                    os.close(slave)
                if master is not None:
                    os.close(master)

        rendered = bytes(output)
        self.assertIn(b"\x1b[?1049h", rendered)
        self.assertIn(b"K.I.T.T.", rendered)
        self.assertIn(b"Command Palette", rendered)
        self.assertIn(b"\x1b[?1049l", rendered)
        self.assertNotIn(b"kitt [TUI]>", rendered)


if __name__ == "__main__":
    unittest.main()
