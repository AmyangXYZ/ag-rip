"""Serve the compare page (this folder) on http://localhost:8765, with byte ranges.

`python -m http.server` ignores Range requests, and Chrome cannot seek a <video> or
<audio> it cannot fetch by range: every seek of the Unity video falls back to its
start, so it flashed its first frames whenever the page put it back in step with the
audio, and paused frames other than the first never showed. This server answers Range
with 206 Partial Content, which makes both seekable.

    python serve.py [port]
"""
import http.server
import os
import re
import sys


class RangeHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def send_head(self):
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        path = self.translate_path(self.path)
        if not m or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        a, b = m.groups()
        if a:
            start, end = int(a), min(int(b) if b else size - 1, size - 1)
        elif b:  # the last b bytes
            start, end = max(0, size - int(b)), size - 1
        else:
            return super().send_head()
        if start >= size or start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Last-Modified", self.date_time_string(int(os.path.getmtime(path))))
        self.end_headers()
        self._left = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        left = getattr(self, "_left", None)
        if left is None:
            return super().copyfile(source, outputfile)
        self._left = None
        while left > 0:
            buf = source.read(min(1 << 16, left))
            if not buf:
                break
            try:
                outputfile.write(buf)
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                break  # the browser drops range requests it no longer needs
            left -= len(buf)

    def log_message(self, fmt, *args):
        pass


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    server = http.server.ThreadingHTTPServer(("", port), RangeHandler)
    print(f"compare page: http://localhost:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
