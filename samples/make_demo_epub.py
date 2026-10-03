#!/usr/bin/env python3
"""Build samples/pyreader-demo.epub: a small EPUB 3 that exercises pyReader.

It contains public-domain prose (the opening of Lewis Carroll's "Alice's
Adventures in Wonderland", 1865), a science chapter with MathML equations,
a table, code, a footnote link, and a high-resolution generated image.

Requires Pillow for the image:  python3 make_demo_epub.py
"""
import io
import os
import uuid
import zipfile

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "pyreader-demo.epub")


def mandelbrot(w=1600, h=1000, iters=90):
    """A Mandelbrot set in the Midnight Ink palette (sky -> lavender -> rose)."""
    stops = [(25, 25, 38), (125, 211, 252), (196, 181, 253), (249, 168, 212), (253, 230, 138)]

    def palette(t):
        t = max(0.0, min(1.0, t)) * (len(stops) - 1)
        i = min(int(t), len(stops) - 2)
        f = t - i
        a, b = stops[i], stops[i + 1]
        return tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3))

    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        ci = (y - h / 2) * 2.4 / h
        for x in range(w):
            cr = (x - w * 0.62) * 2.4 / h
            zr = zi = 0.0
            n = 0
            while zr * zr + zi * zi < 4.0 and n < iters:
                zr, zi = zr * zr - zi * zi + cr, 2 * zr * zi + ci
                n += 1
            px[x, y] = (25, 25, 38) if n == iters else palette((n / iters) ** 0.45)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


XHTML = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">
<head><meta charset="utf-8"/><title>{title}</title><link rel="stylesheet" href="../css/book.css"/></head>
<body>
{body}
</body>
</html>
"""

CSS = """
/* Book-specific typography: pyReader keeps these fonts and spacings. */
.epigraph { font-style: italic; text-align: right; margin: 0 0 2em 30%; }
.poem { font-family: "Noto Serif", serif; font-style: italic; text-align: left; margin: 1em 2em; }
.poem p { text-indent: 0; }
.sans { font-family: "Noto Sans", sans-serif; }
table.data th { font-family: "Noto Sans", sans-serif; font-weight: 600; }
"""

CH1 = """
<h1>Down the Rabbit-Hole</h1>
<p class="epigraph">Alice’s Adventures in Wonderland — Lewis Carroll, 1865</p>
<p>Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: once or twice she had peeped into the book her sister was reading, but it had no pictures or conversations in it, “and what is the use of a book,” thought Alice “without pictures or conversations?”</p>
<p>So she was considering in her own mind (as well as she could, for the hot day made her feel very sleepy and stupid), whether the pleasure of making a daisy-chain would be worth the trouble of getting up and picking the daisies, when suddenly a White Rabbit with pink eyes ran close by her.</p>
<p>There was nothing so <em>very</em> remarkable in that; nor did Alice think it so <em>very</em> much out of the way to hear the Rabbit say to itself, “Oh dear! Oh dear! I shall be late!” (when she thought it over afterwards, it occurred to her that she ought to have wondered at this, but at the time it all seemed quite natural); but when the Rabbit actually <em>took a watch out of its waistcoat-pocket</em>, and looked at it, and then hurried on, Alice started to her feet, for it flashed across her mind that she had never before seen a rabbit with either a waistcoat-pocket, or a watch to take out of it, and burning with curiosity, she ran across the field after it, and fortunately was just in time to see it pop down a large rabbit-hole under the hedge.</p>
<p>In another moment down went Alice after it, never once considering how in the world she was to get out again.</p>
<p>The rabbit-hole went straight on like a tunnel for some way, and then dipped suddenly down, so suddenly that Alice had not a moment to think about stopping herself before she found herself falling down a very deep well.</p>
<p>Either the well was very deep, or she fell very slowly, for she had plenty of time as she went down to look about her and to wonder what was going to happen next. First, she tried to look down and make out what she was coming to, but it was too dark to see anything; then she looked at the sides of the well, and noticed that they were filled with cupboards and book-shelves; here and there she saw maps and pictures hung upon pegs. She took down a jar from one of the shelves as she passed; it was labelled “ORANGE MARMALADE”, but to her great disappointment it was empty: she did not like to drop the jar for fear of killing somebody underneath, so managed to put it into one of the cupboards as she fell past it.</p>
<p>“Well!” thought Alice to herself, “after such a fall as this, I shall think nothing of tumbling down stairs! How brave they’ll all think me at home! Why, I wouldn’t say anything about it, even if I fell off the top of the house!” (Which was very likely true.)</p>
<p>Down, down, down. Would the fall <em>never</em> come to an end? “I wonder how many miles I’ve fallen by this time?” she said aloud. “I must be getting somewhere near the centre of the earth. Let me see: that would be four thousand miles down, I think—” (for, you see, Alice had learnt several things of this sort in her lessons in the schoolroom, and though this was not a <em>very</em> good opportunity for showing off her knowledge, as there was no one to listen to her, still it was good practice to say it over) “—yes, that’s about the right distance—but then I wonder what Latitude or Longitude I’ve got to?” (Alice had no idea what Latitude was, or Longitude either, but thought they were nice grand words to say.)</p>
<p>Presently she began again. “I wonder if I shall fall right <em>through</em> the earth! How funny it’ll seem to come out among the people that walk with their heads downward! The Antipathies, I think—” (she was rather glad there <em>was</em> no one listening, this time, as it didn’t sound at all the right word) “—but I shall have to ask them what the name of the country is, you know. Please, Ma’am, is this New Zealand or Australia?” (and she tried to curtsey as she spoke—fancy <em>curtseying</em> as you’re falling through the air! Do you think you could manage it?) “And what an ignorant little girl she’ll think me for asking! No, it’ll never do to ask: perhaps I shall see it written up somewhere.”</p>
<hr/>
<div class="poem">
<p>How doth the little crocodile</p>
<p>Improve his shining tail,</p>
<p>And pour the waters of the Nile</p>
<p>On every golden scale!</p>
</div>
"""

CH2 = """
<h1>Equations &amp; Images</h1>
<p>This chapter checks that pyReader renders scientific writing: inline mathematics such as
<math><mi>E</mi><mo>=</mo><mi>m</mi><msup><mi>c</mi><mn>2</mn></msup></math>
or <math><msup><mi>e</mi><mrow><mi>i</mi><mi>π</mi></mrow></msup><mo>+</mo><mn>1</mn><mo>=</mo><mn>0</mn></math>
sits comfortably within a line of text, while larger results are set on their own line.<a href="#fn1" id="r1"><sup>1</sup></a></p>
<p>The roots of a quadratic equation <math><mi>a</mi><msup><mi>x</mi><mn>2</mn></msup><mo>+</mo><mi>b</mi><mi>x</mi><mo>+</mo><mi>c</mi><mo>=</mo><mn>0</mn></math> are</p>
<math display="block"><mi>x</mi><mo>=</mo><mfrac><mrow><mo>−</mo><mi>b</mi><mo>±</mo><msqrt><msup><mi>b</mi><mn>2</mn></msup><mo>−</mo><mn>4</mn><mi>a</mi><mi>c</mi></msqrt></mrow><mrow><mn>2</mn><mi>a</mi></mrow></mfrac></math>
<p>The Gaussian integral, which turns up everywhere from statistics to quantum mechanics:</p>
<math display="block"><msubsup><mo>∫</mo><mrow><mo>−</mo><mi>∞</mi></mrow><mi>∞</mi></msubsup><msup><mi>e</mi><mrow><mo>−</mo><msup><mi>x</mi><mn>2</mn></msup></mrow></msup><mspace width="0.2em"/><mi>d</mi><mi>x</mi><mo>=</mo><msqrt><mi>π</mi></msqrt></math>
<p>Maxwell’s equations in differential form describe every classical electromagnetic phenomenon:</p>
<math display="block"><mtable columnalign="right left">
<mtr><mtd><mo>∇</mo><mo>·</mo><mi mathvariant="bold">E</mi></mtd><mtd><mo>=</mo><mfrac><mi>ρ</mi><msub><mi>ε</mi><mn>0</mn></msub></mfrac></mtd></mtr>
<mtr><mtd><mo>∇</mo><mo>·</mo><mi mathvariant="bold">B</mi></mtd><mtd><mo>=</mo><mn>0</mn></mtd></mtr>
<mtr><mtd><mo>∇</mo><mo>×</mo><mi mathvariant="bold">E</mi></mtd><mtd><mo>=</mo><mo>−</mo><mfrac><mrow><mo>∂</mo><mi mathvariant="bold">B</mi></mrow><mrow><mo>∂</mo><mi>t</mi></mrow></mfrac></mtd></mtr>
<mtr><mtd><mo>∇</mo><mo>×</mo><mi mathvariant="bold">B</mi></mtd><mtd><mo>=</mo><msub><mi>μ</mi><mn>0</mn></msub><mi mathvariant="bold">J</mi><mo>+</mo><msub><mi>μ</mi><mn>0</mn></msub><msub><mi>ε</mi><mn>0</mn></msub><mfrac><mrow><mo>∂</mo><mi mathvariant="bold">E</mi></mrow><mrow><mo>∂</mo><mi>t</mi></mrow></mfrac></mtd></mtr>
</mtable></math>
<p>Chemistry reads just as well — photosynthesis in one line:</p>
<math display="block"><mn>6</mn><msub><mi>CO</mi><mn>2</mn></msub><mo>+</mo><mn>6</mn><msub><mi mathvariant="normal">H</mi><mn>2</mn></msub><mi mathvariant="normal">O</mi><mover><mo>⟶</mo><mtext>light</mtext></mover><msub><mi mathvariant="normal">C</mi><mn>6</mn></msub><msub><mi mathvariant="normal">H</mi><mn>12</mn></msub><msub><mi mathvariant="normal">O</mi><mn>6</mn></msub><mo>+</mo><mn>6</mn><msub><mi mathvariant="normal">O</mi><mn>2</mn></msub></math>
<h2>A picture</h2>
<p>Images are drawn at their native resolution and are never stretched beyond it, so fine detail stays sharp.</p>
<figure>
<img src="../images/mandelbrot.png" alt="The Mandelbrot set" width="1600" height="1000"/>
<figcaption>The Mandelbrot set, <math><msub><mi>z</mi><mrow><mi>n</mi><mo>+</mo><mn>1</mn></mrow></msub><mo>=</mo><msubsup><mi>z</mi><mi>n</mi><mn>2</mn></msubsup><mo>+</mo><mi>c</mi></math>, rendered at 1600 × 1000.</figcaption>
</figure>
<h2>A table</h2>
<table class="data">
<tr><th>Constant</th><th>Symbol</th><th>Value</th></tr>
<tr><td>Speed of light</td><td><math><mi>c</mi></math></td><td>299 792 458 m/s</td></tr>
<tr><td>Planck constant</td><td><math><mi>h</mi></math></td><td>6.626 × 10<sup>−34</sup> J·s</td></tr>
<tr><td>Gravitational constant</td><td><math><mi>G</mi></math></td><td>6.674 × 10<sup>−11</sup> m³/(kg·s²)</td></tr>
</table>
<h2>Some code</h2>
<pre><code>def fib(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a</code></pre>
<blockquote><p>Block quotations are set off with an accent rule, the way pull-quotes are set in a printed book.</p></blockquote>
<p class="sans">This paragraph asks for a sans-serif face in the book’s own stylesheet, and pyReader honours it.</p>
<hr/>
<p id="fn1"><a href="#r1">1.</a> Footnote links work: click the number to jump back.</p>
"""

NAV = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Contents</title></head>
<body><nav epub:type="toc"><ol>
<li><a href="text/ch1.xhtml">Down the Rabbit-Hole</a></li>
<li><a href="text/ch2.xhtml">Equations &amp; Images</a></li>
</ol></nav></body></html>
"""

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="uid">urn:uuid:{uid}</dc:identifier>
<dc:title>pyReader Demo</dc:title>
<dc:creator>Lewis Carroll &amp; friends</dc:creator>
<dc:language>en</dc:language>
<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
</metadata>
<manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="css" href="css/book.css" media-type="text/css"/>
<item id="ch1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>
<item id="ch2" href="text/ch2.xhtml" media-type="application/xhtml+xml" properties="mathml"/>
<item id="img" href="images/mandelbrot.png" media-type="image/png"/>
</manifest>
<spine><itemref idref="ch1"/><itemref idref="ch2"/></spine>
</package>
"""

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""


def main():
    with zipfile.ZipFile(OUT, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", OPF.format(uid=uuid.uuid4()), compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/nav.xhtml", NAV, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/css/book.css", CSS, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/text/ch1.xhtml", XHTML.format(title="Down the Rabbit-Hole", body=CH1),
                   compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/text/ch2.xhtml", XHTML.format(title="Equations & Images", body=CH2),
                   compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/images/mandelbrot.png", mandelbrot(), compress_type=zipfile.ZIP_STORED)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
