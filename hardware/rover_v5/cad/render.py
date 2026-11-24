"""Render scene specs (JSON list) to PNGs with headless Chromium + three.js. Run with the repo venv python."""
import base64, json, sys, threading, http.server, socketserver, os, functools, io
from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont

ROOT = os.environ.get("ROVER_V5_RENDER_ROOT", os.path.dirname(os.path.abspath(__file__)))
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONTB = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass

def serve():
    h = functools.partial(Quiet, directory=ROOT)
    srv = socketserver.TCPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

def annotate(png_bytes, labels, title=None, scale=1):
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype(FONT, int(15 * scale)); fb = ImageFont.truetype(FONTB, int(22 * scale))
    for l in labels:
        x, y, t = l["x"], l["y"], l["text"]
        tw = d.textlength(t, font=f)
        bx = (x + 10 * scale, y - 26 * scale)
        d.line([(x, y), bx], fill="#c0392b", width=max(1, int(2 * scale)))
        d.ellipse([x - 3*scale, y - 3*scale, x + 3*scale, y + 3*scale], fill="#c0392b")
        d.rectangle([bx[0], bx[1] - 11*scale, bx[0] + tw + 10*scale, bx[1] + 11*scale], fill="#ffffff", outline="#c0392b")
        d.text((bx[0] + 5*scale, bx[1]), t, fill="#111", font=f, anchor="lm")
    if title:
        d.rectangle([0, 0, im.width, 40 * scale], fill="#ffffff")
        d.text((16 * scale, 20 * scale), title, fill="#111", font=fb, anchor="lm")
    return im

def render_all(specs, outdir):
    srv = serve(); port = srv.server_address[1]
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        pg = b.new_page(viewport={"width": 400, "height": 300})
        pg.on("console", lambda m: print("console:", m.text) if "missing" in m.text else None)
        pg.goto(f"http://127.0.0.1:{port}/render/render.html"); pg.wait_for_function("window.ready === true")
        for s in specs:
            res = pg.evaluate("s => renderScene(s)", s)
            png = base64.b64decode(res["png"].split(",", 1)[1])
            im = annotate(png, res["labels"], s.get("title"), s.get("scale", 1))
            im.save(os.path.join(outdir, s["out"]))
            print("wrote", s["out"])
        b.close()
    srv.shutdown()

if __name__ == "__main__":
    specs = json.load(open(sys.argv[1]))
    render_all(specs, sys.argv[2])
