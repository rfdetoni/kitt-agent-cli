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
            process = subprocess.Popen(
                [sys.executable, "-m", "kitt.cli.main", "--ui", "tui", "--no-history", "--no-animation", "--root", root],
                stdin=slave, stdout=slave, stderr=slave, cwd=os.path.dirname(os.path.dirname(__file__)), env=env,
                close_fds=True,
            )
            os.close(slave)
            output = bytearray()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and b"K.I.T.T." not in output:
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    output.extend(os.read(master, 65536))
            os.write(master, b"\x10")  # palette
            time.sleep(0.1)
            os.write(master, b"\x1b\x04")  # close overlay, exit
            process.wait(timeout=5)
            while True:
                ready, _, _ = select.select([master], [], [], 0.05)
                if not ready:
                    break
                try: output.extend(os.read(master, 65536))
                except OSError: break
            os.close(master)

        rendered = bytes(output)
        self.assertEqual(process.returncode, 0)
        self.assertIn(b"\x1b[?1049h", rendered)
        self.assertIn(b"K.I.T.T.", rendered)
        self.assertIn(b"Command Palette", rendered)
        self.assertIn(b"\x1b[?1049l", rendered)
        self.assertNotIn(b"kitt [TUI]>", rendered)


if __name__ == "__main__":
    unittest.main()
