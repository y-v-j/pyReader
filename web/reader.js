/* pyReader — reading engine.
 *
 * Each chapter is loaded into a same-origin iframe and laid out with CSS
 * columns: one column per page, two facing pages per "spread" when there is
 * room. Turning a page scrolls the iframe by one spread; the page-turn
 * animation is a 3D "leaf" that swings over the spine.
 *
 * Python talks to this page with PYR.open(...) / PYR.settings(...) /
 * PYR.toast(...); the page answers through console messages prefixed "PYR:".
 */
"use strict";

const PYR = (() => {
  const $ = (id) => document.getElementById(id);
  const frame = $("book");
  const BASE_PX = 18;            // body text size at 100%
  const MIN_SCALE = 0.6, MAX_SCALE = 2.4;

  const S = {
    book: null, settings: {}, scale: 1,
    chapter: 0, page: 0, pages: 1,
    highlights: {}, L: null,
    loadToken: 0, animating: false, busy: false,
    back: [],                    // places left by following links (Shift+← returns)
  };

  const send = (msg) => console.log("PYR:" + JSON.stringify(msg));
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const doc = () => frame.contentDocument;
  const win = () => frame.contentWindow;
  const chapter = () => S.book.chapters[S.chapter];
  const nextFrame = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

  // ── Toast ────────────────────────────────────────────────────────────────
  let toastTimer = null;
  function toast(text, kind) {
    const t = $("toast");
    t.textContent = text;
    t.className = "show" + (kind === "error" ? " error" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.className = kind === "error" ? "error" : ""), kind === "error" ? 6000 : 1800);
  }

  // ── Layout ───────────────────────────────────────────────────────────────
  function computeLayout() {
    const stage = $("stage");
    const W = stage.clientWidth, H = stage.clientHeight;
    const mode = S.settings.layout || "auto";
    const spread = mode === "spread" || (mode === "auto" && W >= 860);
    const G = 26;                                         // gap between facing pages
    const cardW = spread ? Math.floor((W - G) / 2) : W;   // one page spans the whole window
    const offset = 0;
    const PX = Math.round(clamp(cardW * 0.075, 26, 60)); // side margins inside a page
    const PT = 34, PB = 44;                               // top margin, bottom (folio) margin
    const colW = cardW - 2 * PX;
    const colGap = spread ? G + 2 * PX : 2 * PX;
    return {
      W, H, G, spread, cardW, offset, PX, PT, PB, colW, colGap,
      perView: spread ? 2 : 1,
      frameW: spread ? 2 * colW + colGap : colW,
      frameH: H - PT - PB,
    };
  }

  function applyLayout(L) {
    const px = (v) => v + "px";
    const pl = $("pageL"), pr = $("pageR"), spine = $("spine");
    Object.assign(pl.style, { left: px(L.offset), width: px(L.cardW), height: px(L.H) });
    pr.style.display = L.spread ? "block" : "none";
    spine.style.display = L.spread ? "block" : "none";
    if (L.spread) {
      Object.assign(pr.style, { left: px(L.cardW + L.G), width: px(L.cardW), height: px(L.H) });
      Object.assign(spine.style, { left: px(L.cardW - 10), width: px(L.G + 20) });
    }
    Object.assign(frame.style, { left: px(L.offset + L.PX), top: px(L.PT), width: px(L.frameW), height: px(L.frameH) });
  }

  // ── Book styling injected into each chapter ─────────────────────────────
  function baseCss() {
    // Inserted *before* the book's own CSS, so the book's fonts and spacing win.
    const body = (S.settings.bodyFont || "Noto Serif").replace(/"/g, "");
    return `
      body { font-family: "${body}", "Noto Serif", "Liberation Serif", serif; line-height: 1.62;
             text-align: ${S.settings.justify === false ? "left" : "justify"}; hyphens: auto; text-wrap: pretty; -webkit-hyphens: auto; font-kerning: normal;
             text-rendering: optimizeLegibility; font-variant-ligatures: common-ligatures; }
      p { margin: 0 0 0.2em; text-indent: 1.4em; orphans: 2; widows: 2; }
      h1, h2, h3, h4, h5, h6 { font-family: var(--pyr-ui); line-height: 1.25; text-align: left;
             text-indent: 0; margin: 0.3em 0 0.9em; hyphens: manual; break-after: avoid; }
      h1 { font-size: 1.75em; } h2 { font-size: 1.45em; } h3 { font-size: 1.2em; } h4, h5, h6 { font-size: 1em; }
      h1 + p, h2 + p, h3 + p, h4 + p, h5 + p, h6 + p, hr + p, blockquote + p { text-indent: 0; }
      blockquote { margin: 1em 0 1em 0.2em; padding: 0.15em 0 0.15em 1em; font-style: italic; }
      pre, code, kbd, samp, tt { font-family: var(--pyr-ui); font-size: 0.92em; }
      pre { white-space: pre-wrap; text-align: left; padding: 0.6em 0.8em; border-radius: 8px; }
      figure { margin: 1em 0; text-align: center; break-inside: avoid; }
      figcaption, .caption { font-size: 0.85em; font-style: italic; text-align: center; text-indent: 0; }
      table { border-collapse: collapse; margin: 1em auto; font-size: 0.9em; break-inside: avoid; }
      td, th { padding: 0.3em 0.55em; border: 1px solid; text-align: left; hyphens: manual; }
      math[display="block"] { margin: 0.8em 0; }
      sup, sub { line-height: 0; }
    `;
  }

  function overrideCss(L) {
    // Appended *after* the book's CSS: page geometry and dark-theme colours.
    const fontPx = (BASE_PX * S.scale).toFixed(2);
    return `
      :root { --pyr-ui: "FantasqueSansM Nerd Font", "Fantasque Sans Mono", monospace; }
      /* A real OpenType math font draws radicals, integrals and stretchy brackets properly */
      @font-face { font-family: "pyReader Math"; src: local("STIX Two Math"), url("/app/fonts/STIXTwoMath-Regular.otf"); }
      math { font-family: "pyReader Math", "STIX Two Math", "Noto Sans Math", math; }
      html { height: ${L.frameH}px !important; width: auto !important; margin: 0 !important; padding: 0 !important;
             column-width: ${L.colW}px !important; column-gap: ${L.colGap}px !important; column-fill: auto !important;
             overflow: hidden !important; background: transparent !important; font-size: ${fontPx}px !important;
             color-scheme: dark; user-select: none; }
      html.pyr-select { user-select: text; cursor: text; }
      body { margin: 0 !important; padding: 0 !important; width: auto !important; max-width: none !important;
             height: auto !important; min-height: 0 !important; columns: auto !important; overflow: visible !important;
             background: transparent !important; color: ${S.settings.textColor || "#f5f5f7"} !important; font-size: 1rem !important; }
      body * { color: inherit !important; background-color: transparent !important;
               border-color: rgba(196, 181, 253, 0.28) !important; max-width: 100% !important; }
      h1, h1 * { color: #c4b5fd !important; }
      h2, h2 * { color: #7dd3fc !important; }
      h3, h3 * { color: #f9a8d4 !important; }
      h4, h5, h6, h4 *, h5 *, h6 * { color: #9a9ab8 !important; }
      a, a * { color: #f9a8d4 !important; text-decoration: none !important; }
      a { border-bottom: 1px dotted rgba(249, 168, 212, 0.6) !important; }
      blockquote { border-left: 3px solid #c4b5fd !important; }
      hr { border: 0 !important; height: 2px !important; margin: 1.4em 18% !important; opacity: 0.75;
           background: linear-gradient(90deg, #7dd3fc, #c4b5fd, #f9a8d4) !important; }
      pre { background-color: rgba(255, 255, 255, 0.035) !important; }
      figcaption, figcaption *, .caption, .caption * { color: #9a9ab8 !important; }
      img, svg, video, object { max-height: ${L.frameH - 6}px !important; object-fit: contain;
                               break-inside: avoid; image-rendering: auto; }
      img { height: auto; }
      ::selection { background: rgba(125, 211, 252, 0.32); }
      mark.pyr-hl { background: rgba(253, 230, 138, 0.26) !important; color: #fff3c4 !important;
                    border-radius: 3px; box-shadow: inset 0 -2px 0 rgba(253, 230, 138, 0.75);
                    -webkit-box-decoration-break: clone; box-decoration-break: clone; }
      .pyr-dropcap::first-letter { float: left; font-family: var(--pyr-ui); font-weight: bold; font-size: 3.4em;
                    line-height: 0.82; margin: 0.06em 0.1em 0 0; color: #c4b5fd !important; }
    `;
  }

  function injectStyles(d) {
    const head = d.head || d.getElementsByTagName("head")[0] || d.documentElement;
    let base = d.getElementById("pyr-base"), over = d.getElementById("pyr-over");
    if (!base) {
      base = d.createElement("style");
      base.id = "pyr-base";
      head.insertBefore(base, head.firstChild);
    }
    if (!over) {
      over = d.createElement("style");
      over.id = "pyr-over";
    }
    head.appendChild(over);                       // always last, after the book's CSS
    base.textContent = baseCss();
    over.textContent = overrideCss(S.L);
  }

  // ── MathML fix-ups ───────────────────────────────────────────────────────
  // Chromium implements MathML Core, which ignores mathvariant (except
  // "normal") and columnalign. Convert styled letters to the Unicode
  // mathematical alphanumerics and align table columns with CSS instead.
  const MATH_ALPHA = {
    "bold": [0x1D400, 0x1D41A, 0x1D7CE], "italic": [0x1D434, 0x1D44E, null],
    "bold-italic": [0x1D468, 0x1D482, null], "script": [0x1D49C, 0x1D4B6, null],
    "bold-script": [0x1D4D0, 0x1D4EA, null], "fraktur": [0x1D504, 0x1D51E, null],
    "double-struck": [0x1D538, 0x1D552, 0x1D7D8], "bold-fraktur": [0x1D56C, 0x1D586, null],
    "sans-serif": [0x1D5A0, 0x1D5BA, 0x1D7E2], "bold-sans-serif": [0x1D5D4, 0x1D5EE, 0x1D7EC],
    "sans-serif-italic": [0x1D608, 0x1D622, null], "monospace": [0x1D670, 0x1D68A, 0x1D7F6],
  };
  const MATH_HOLES = {
    "italic": { h: 0x210E },
    "script": { B: 0x212C, E: 0x2130, F: 0x2131, H: 0x210B, I: 0x2110, L: 0x2112, M: 0x2133, R: 0x211B,
                e: 0x212F, g: 0x210A, o: 0x2134 },
    "fraktur": { C: 0x212D, H: 0x210C, I: 0x2111, R: 0x211C, Z: 0x2128 },
    "double-struck": { C: 0x2102, H: 0x210D, N: 0x2115, P: 0x2119, Q: 0x211A, R: 0x211D, Z: 0x2124 },
  };

  function mathAlpha(text, variant) {
    const base = MATH_ALPHA[variant];
    if (!base) return null;
    const holes = MATH_HOLES[variant] || {};
    return [...text].map((ch) => {
      if (holes[ch]) return String.fromCodePoint(holes[ch]);
      const c = ch.codePointAt(0);
      if (c >= 65 && c <= 90) return String.fromCodePoint(base[0] + c - 65);
      if (c >= 97 && c <= 122) return String.fromCodePoint(base[1] + c - 97);
      if (c >= 48 && c <= 57 && base[2]) return String.fromCodePoint(base[2] + c - 48);
      return ch;
    }).join("");
  }

  function fixMath(d) {
    for (const el of d.querySelectorAll("mi[mathvariant], mn[mathvariant], mo[mathvariant], mtext[mathvariant]")) {
      const v = el.getAttribute("mathvariant");
      if (v === "normal" || el.children.length) continue;
      const out = mathAlpha(el.textContent, v);
      if (out !== null) { el.textContent = out; el.setAttribute("mathvariant", "normal"); }
    }
    for (const table of d.querySelectorAll("mtable[columnalign]")) {
      const aligns = table.getAttribute("columnalign").trim().split(/\s+/);
      for (const row of table.children) {
        [...row.children].forEach((cell, i) => {
          cell.style.textAlign = aligns[Math.min(i, aligns.length - 1)];
          cell.style.paddingInline = "0.15em";
        });
      }
    }
  }

  function decorate(d) {
    fixMath(d);
    // Drop cap on the first real paragraph after the chapter heading
    // (skipping short lines such as epigraphs or bylines)
    const heading = d.querySelector("h1, h2");
    if (heading) {
      const paras = [...d.querySelectorAll("p")].filter(
        (p) => heading.compareDocumentPosition(p) & Node.DOCUMENT_POSITION_FOLLOWING).slice(0, 4);
      const p = paras.find((p) => p.textContent.trim().length > 120);
      if (p && /^[A-Za-z0-9“"‘']/.test(p.textContent.trim())) p.classList.add("pyr-dropcap");
    }
    // Never upscale an image past its natural size: small images stay crisp
    for (const img of d.images) {
      const fix = () => {
        if (img.naturalWidth > 0) img.style.setProperty("max-width", `min(100%, ${img.naturalWidth}px)`, "important");
      };
      img.complete ? fix() : img.addEventListener("load", fix, { once: true });
    }
  }

  // ── Chapters & pagination ────────────────────────────────────────────────
  function bookUrl(path) {
    return "/book/" + S.book.id + "/" + path.split("/").map(encodeURIComponent).join("/");
  }

  function loadChapter(index, opts = {}) {
    S.chapter = clamp(index, 0, S.book.chapters.length - 1);
    const token = ++S.loadToken;
    S.busy = true;
    frame.style.opacity = 0;
    frame.onload = () => { if (token === S.loadToken) onChapterLoaded(opts, token); };
    frame.src = bookUrl(chapter().href) + (opts.asHtml ? "?as=html" : "");
    updateChrome();
  }

  async function onChapterLoaded(opts, token) {
    const d = doc();
    if (!d || !d.documentElement) { S.busy = false; return; }
    if (!opts.asHtml && d.getElementsByTagName("parsererror").length) {
      return loadChapter(S.chapter, { ...opts, asHtml: true });   // not well-formed XHTML
    }
    injectStyles(d);
    decorate(d);
    await hyphenate(d);
    if (token !== S.loadToken) return;
    applyHighlights(d);
    wireDocument(d);
    try { await d.fonts.ready; } catch (e) { /* ignore */ }
    await nextFrame();
    if (token !== S.loadToken) return;
    paginate();
    let page = 0;
    if (opts.anchor) page = pageOfAnchor(opts.anchor);
    else if (opts.toEnd) page = S.pages - 1;
    else if (opts.fraction) page = Math.floor(opts.fraction * S.pages + 1e-6);
    showPage(page);
    frame.style.opacity = 1;
    S.busy = false;
    // Late-loading images or fonts can change the page count
    setTimeout(() => { if (token === S.loadToken) repaginateKeepingPlace(); }, 600);
  }

  function paginate() {
    const d = doc(), L = S.L;
    const width = Math.max(d.documentElement.scrollWidth, d.body ? d.body.scrollWidth : 0);
    const cols = Math.max(1, Math.round((width + L.colGap) / (L.colW + L.colGap)));
    S.pages = Math.max(1, Math.ceil(cols / L.perView));
  }

  function scrollToPage() {
    const L = S.L;
    win().scrollTo(S.page * L.perView * (L.colW + L.colGap), 0);
  }

  function showPage(p, animate = false, dir = 1) {
    S.page = clamp(p, 0, S.pages - 1);
    if (animate) turn(dir, scrollToPage);
    else scrollToPage();
    updateChrome();
    reportSoon();
  }

  function repaginateKeepingPlace() {
    if (!S.book || !doc()) return;
    const frac = S.pages > 0 ? S.page / S.pages : 0;
    paginate();
    showPage(Math.floor(frac * S.pages + 1e-6));
  }

  async function relayout() {
    if (!S.book) return;
    const frac = S.pages > 0 ? S.page / S.pages : 0;
    S.L = computeLayout();
    applyLayout(S.L);
    const d = doc();
    if (!d || !d.getElementById("pyr-over")) return;
    injectStyles(d);
    await nextFrame();
    paginate();
    showPage(Math.floor(frac * S.pages + 1e-6));
  }

  function pageOfAnchor(id) {
    const d = doc();
    const el = d.getElementById(id) || d.getElementsByName(id)[0];
    if (!el) return 0;
    const x = el.getBoundingClientRect().left + win().scrollX;
    const col = Math.floor((x + 1) / (S.L.colW + S.L.colGap));
    return clamp(Math.floor(col / S.L.perView), 0, S.pages - 1);
  }

  // ── Hyphenation (soft hyphens from Python's Pyphen, in the book's language) ─
  const SHY = /\u00AD/g;
  const clean = (s) => s.replace(SHY, "");
  const LONG_WORD = /\p{L}{6,}/gu;
  const pendingHyph = new Map();
  let hyphToken = 0;

  function hyphenatable(d) {
    return textNodes(d).filter((n) => !unwrappable(n) && !n.parentElement.closest("h1, h2, h3, h4, h5, h6, pre, code, kbd, samp"));
  }

  function hyphenate(d) {
    if (S.settings.hyphenate === false) return Promise.resolve();
    const nodes = hyphenatable(d);
    const words = new Set();
    for (const n of nodes) for (const w of n.nodeValue.match(LONG_WORD) || []) words.add(w);
    if (!words.size) return Promise.resolve();
    const lang = d.documentElement.getAttribute("lang") || d.documentElement.getAttribute("xml:lang")
                 || S.book.language || "en";
    const token = ++hyphToken;
    return new Promise((resolve) => {
      const finish = (map) => {
        pendingHyph.delete(token);
        if (map) for (const n of nodes) n.nodeValue = n.nodeValue.replace(LONG_WORD, (w) => map[w] || w);
        resolve();
      };
      pendingHyph.set(token, finish);
      setTimeout(() => pendingHyph.has(token) && finish(null), 2500);   // never block reading
      send({ t: "hyph", token, lang, words: [...words] });
    });
  }

  function hyphenated(token, map) {
    const finish = pendingHyph.get(token);
    if (finish) finish(map);
  }

  // ── Turning pages ────────────────────────────────────────────────────────
  function next() {
    if (!S.book || S.busy) return;
    if (S.animating) finishTurn();
    if (S.page < S.pages - 1) showPage(S.page + 1, animationsOn(), 1);
    else if (S.chapter < S.book.chapters.length - 1) turnChapter(1);
    else toast("The end ✦");
  }

  function prev() {
    if (!S.book || S.busy) return;
    if (S.animating) finishTurn();
    if (S.page > 0) showPage(S.page - 1, animationsOn(), -1);
    else if (S.chapter > 0) turnChapter(-1);
    else toast("Beginning of the book");
  }

  function turnChapter(dir) {
    const go = () => loadChapter(S.chapter + dir, dir > 0 ? { fraction: 0 } : { toEnd: true });
    animationsOn() ? turn(dir, go) : go();
  }

  const animationsOn = () => S.settings.animations !== false;
  let currentTurn = null;

  function turn(dir, swap) {
    const L = S.L, leaf = $("leaf"), fwd = dir > 0;
    S.animating = true;
    const left = L.spread ? (fwd ? L.cardW + L.G : 0) : L.offset;
    Object.assign(leaf.style, {
      display: "block", left: left + "px", width: L.cardW + "px", height: L.H + "px",
      transformOrigin: fwd ? "left center" : "right center",
    });
    const end = L.spread ? (fwd ? -180 : 180) : (fwd ? -100 : 100);
    const dur = L.spread ? 560 : 440;
    const fade = frame.animate([{ opacity: 1 }, { opacity: 0.08 }], { duration: 150, fill: "forwards" });
    const anim = leaf.animate([
      { transform: "rotateY(0deg)", filter: "brightness(1)", boxShadow: "0 18px 40px rgba(0,0,0,.45)" },
      { transform: `rotateY(${end / 2}deg)`, filter: "brightness(1.3)", boxShadow: "0 30px 60px rgba(0,0,0,.55)", offset: 0.5 },
      { transform: `rotateY(${end}deg)`, filter: "brightness(0.85)", boxShadow: "0 8px 20px rgba(0,0,0,.3)" },
    ], { duration: dur, easing: "cubic-bezier(.42,.0,.25,1)" });
    let swapped = false;
    const doSwap = () => { if (!swapped) { swapped = true; swap(); } };
    const swapTimer = setTimeout(doSwap, 160);
    const done = () => {
      clearTimeout(swapTimer);
      doSwap();
      leaf.style.display = "none";
      fade.cancel();
      frame.animate([{ opacity: 0.08 }, { opacity: 1 }], { duration: 200, easing: "ease-out" });
      S.animating = false;
      currentTurn = null;
    };
    anim.onfinish = done;
    currentTurn = { anim, done };
  }

  function finishTurn() {
    if (currentTurn) { currentTurn.anim.cancel(); currentTurn.done(); }
  }

  // ── Text size ────────────────────────────────────────────────────────────
  function zoom(dir) {
    if (!S.book) return;
    const v = clamp(Math.round((S.scale + dir * 0.1) * 10) / 10, MIN_SCALE, MAX_SCALE);
    if (v === S.scale) { toast(dir > 0 ? "Largest text size" : "Smallest text size"); return; }
    S.scale = v;
    send({ t: "scale", v });
    relayout();
    toast(`Text size ${Math.round(v * 100)}%`);
  }

  // ── Input ────────────────────────────────────────────────────────────────
  function setSelectMode(on) {
    const d = doc();
    if (d && d.documentElement) d.documentElement.classList.toggle("pyr-select", on);
  }

  function onKeyDown(e) {
    if (e.key === "Control") { setSelectMode(true); return; }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    switch (e.key) {
      case "ArrowRight": case "PageDown": case " ": next(); break;
      case "ArrowLeft": case "Backspace": e.shiftKey ? goBack() : prev(); break;
      case "PageUp": prev(); break;
      case "a": case "A": zoom(1); break;
      case "z": case "Z": zoom(-1); break;
      case "Home": if (S.book) showPage(0); break;
      case "End": if (S.book) showPage(S.pages - 1); break;
      default: return;
    }
    e.preventDefault();
  }

  function onKeyUp(e) {
    if (e.key === "Control") setSelectMode(false);
  }

  function wireDocument(d) {
    d.addEventListener("keydown", onKeyDown);
    d.addEventListener("keyup", onKeyUp);
    d.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      if (!e.ctrlKey) {                       // plain clicks never start a selection
        e.preventDefault();
        d.getSelection().removeAllRanges();
        window.focus();
        return;
      }
      setSelectMode(true);
    }, true);
    d.addEventListener("mouseup", (e) => {
      const sel = d.getSelection();
      if (!e.ctrlKey || !sel || sel.isCollapsed) return;
      if (e.shiftKey) {
        addHighlight(d, sel.getRangeAt(0));
        sel.removeAllRanges();
      } else {
        toast("Selected · Ctrl+C to copy");
      }
    }, true);
    d.addEventListener("click", (e) => {
      const target = e.target && e.target.closest ? e.target : null;
      if (!target) return;
      const mark = target.closest("mark.pyr-hl");
      if (mark && e.ctrlKey && e.shiftKey && d.getSelection().isCollapsed) {
        removeHighlight(d, mark.dataset.id);
        e.preventDefault();
        return;
      }
      const a = target.closest("a[href]");
      if (a && !e.ctrlKey) { e.preventDefault(); followLink(a); }
    }, true);
    d.addEventListener("copy", (e) => {
      const text = clean(d.getSelection().toString());
      if (!text) return;
      e.clipboardData.setData("text/plain", text);   // without the invisible soft hyphens
      e.preventDefault();
      toast("Copied to clipboard");
    });
    d.addEventListener("wheel", (e) => { e.preventDefault(); onWheel(e); }, { passive: false });
  }

  let lastWheel = 0;
  function onWheel(e) {
    const now = Date.now();
    if (!S.book || now - lastWheel < 350 || Math.abs(e.deltaY) + Math.abs(e.deltaX) < 4) return;
    lastWheel = now;
    e.deltaY > 0 || e.deltaX > 0 ? next() : prev();
  }

  function followLink(a) {
    const url = new URL(a.getAttribute("href"), a.ownerDocument.baseURI);
    if (url.protocol !== "pyreader:") { send({ t: "open", url: url.href }); return; }
    const prefix = "/book/" + S.book.id + "/";
    const path = decodeURIComponent(url.pathname);
    if (!path.startsWith(prefix)) return;
    const href = path.slice(prefix.length);
    const anchor = url.hash ? decodeURIComponent(url.hash.slice(1)) : null;
    const idx = S.book.chapters.findIndex((c) => c.href === href);
    if (idx < 0 || (idx === S.chapter && !anchor)) return;
    // Remember the page with the link. Its id, when it has one, finds that
    // page again even if the text size or window changed in the meantime.
    S.back.push({ chapter: S.chapter, fraction: S.pages ? S.page / S.pages : 0, anchor: a.id || null });
    if (S.back.length > 50) S.back.shift();
    if (idx === S.chapter) showPage(pageOfAnchor(anchor), animationsOn(), 1);
    else loadChapter(idx, { anchor });
    toast("Shift+← to go back");
  }

  function goBack() {
    if (!S.book || S.busy) return;
    const place = S.back.pop();
    if (!place) { toast("No link to go back from"); return; }
    if (S.animating) finishTurn();
    if (place.chapter !== S.chapter) {
      loadChapter(place.chapter, place.anchor ? { anchor: place.anchor } : { fraction: place.fraction });
    } else {
      showPage(place.anchor ? pageOfAnchor(place.anchor) : Math.floor(place.fraction * S.pages + 1e-6),
               animationsOn(), -1);
    }
  }

  // ── Highlights (stored as text offsets within the chapter) ──────────────
  function textNodes(d) {
    const root = d.body || d.documentElement;
    const walker = d.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const out = [];
    for (let n = walker.nextNode(); n; n = walker.nextNode()) out.push(n);
    return out;
  }

  const unwrappable = (n) => n.parentElement && n.parentElement.closest("math, svg, script, style, head");

  // Offsets count characters *without* soft hyphens, so highlights survive
  // changes to hyphenation.
  function rawIndex(str, cleanIdx) {
    let seen = 0;
    for (let i = 0; i < str.length; i++) {
      if (seen === cleanIdx) return i;
      if (str[i] !== "\u00AD") seen++;
    }
    return str.length;
  }

  function addHighlight(d, range) {
    let pos = 0, start = null, end = null;
    for (const n of textNodes(d)) {
      const len = clean(n.nodeValue).length;
      if (range.intersectsNode(n)) {
        if (start === null) start = pos + (n === range.startContainer ? clean(n.nodeValue.slice(0, range.startOffset)).length : 0);
        end = pos + (n === range.endContainer ? clean(n.nodeValue.slice(0, range.endOffset)).length : len);
      }
      pos += len;
    }
    if (start === null || end <= start) return;
    const h = { id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), start, end,
                text: clean(range.toString()).slice(0, 300) };
    wrap(d, h);
    const list = (S.highlights[chapter().href] ||= []);
    list.push(h);
    send({ t: "highlights", href: chapter().href, list });
    toast("Highlighted ✦");
  }

  function wrap(d, h) {
    let pos = 0;
    for (const n of textNodes(d)) {
      const len = clean(n.nodeValue).length;
      const cs = Math.max(h.start, pos) - pos, ce = Math.min(h.end, pos + len) - pos;
      pos += len;
      if (ce <= cs || unwrappable(n)) continue;
      const s = rawIndex(n.nodeValue, cs), e = rawIndex(n.nodeValue, ce);
      if (!clean(n.nodeValue.slice(s, e)).trim()) continue;
      let target = n;
      if (s > 0) target = target.splitText(s);
      if (e - s < target.nodeValue.length) target.splitText(e - s);
      const mark = d.createElement("mark");
      mark.className = "pyr-hl";
      mark.dataset.id = h.id;
      mark.title = "Ctrl+Shift+click to remove";
      target.parentNode.insertBefore(mark, target);
      mark.appendChild(target);
    }
  }

  function removeHighlight(d, id) {
    for (const mark of d.querySelectorAll(`mark.pyr-hl[data-id="${CSS.escape(id)}"]`)) {
      const parent = mark.parentNode;
      while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
      parent.removeChild(mark);
      parent.normalize();
    }
    const href = chapter().href;
    const list = (S.highlights[href] || []).filter((h) => h.id !== id);
    if (list.length) S.highlights[href] = list; else delete S.highlights[href];
    send({ t: "highlights", href, list });
    toast("Highlight removed");
  }

  function applyHighlights(d) {
    const list = [...(S.highlights[chapter().href] || [])].sort((a, b) => a.start - b.start);
    for (const h of list) wrap(d, h);
  }

  // ── Chrome: header, folios, progress ────────────────────────────────────
  function updateChrome() {
    if (!S.book) return;
    const ch = chapter(), L = S.L || computeLayout();
    $("chapterTitle").textContent = ch.title && ch.title !== S.book.title ? ch.title : "";
    const first = S.page * L.perView + 1;
    $("folioL").textContent = S.busy ? "" : first;
    $("folioR").textContent = S.busy || !L.spread ? "" : first + 1;
    const chs = S.book.chapters;
    const total = chs.reduce((a, c) => a + c.size, 0);
    const before = chs.slice(0, S.chapter).reduce((a, c) => a + c.size, 0);
    const pct = clamp((before + ch.size * ((S.page + 1) / S.pages)) / total, 0, 1);
    $("progressFill").style.width = (pct * 100).toFixed(2) + "%";
    $("pct").textContent = Math.floor(pct * 100) + "%";
    const pagesTxt = L.spread ? `pages ${first}–${Math.min(first + 1, S.pages * 2)}` : `page ${first}`;
    $("where").textContent = `Section ${S.chapter + 1} of ${chs.length} · ${pagesTxt} of ${S.pages * L.perView}`;
  }

  function reportSoon() {
    // Sent right away (page turns are human-paced) so nothing is lost if the session ends
    send({ t: "pos", chapter: S.chapter, fraction: S.pages ? S.page / S.pages : 0 });
  }

  function tickClock() {
    const d = new Date();
    $("clock").textContent = String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }

  // ── Messages ─────────────────────────────────────────────────────────────
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function showMessage(title, html, isError) {
    S.book = null;
    S.loadToken++;
    frame.removeAttribute("src");
    frame.style.opacity = 0;
    $("message").className = isError ? "error" : "";
    $("messageTitle").textContent = title;
    $("messageBody").innerHTML = html;
    $("title").textContent = "pyReader";
    $("author").textContent = "";
    $("chapterTitle").textContent = "";
    $("folioL").textContent = $("folioR").textContent = "";
    $("where").textContent = "No book open";
    $("pct").textContent = "";
    $("progressFill").style.width = "0";
    S.L = computeLayout();
    applyLayout(S.L);
  }

  // ── Public API (called from Python) ─────────────────────────────────────
  function open(p) {
    S.settings = p.settings || {};
    S.scale = clamp(p.fontScale || 1, MIN_SCALE, MAX_SCALE);
    const howTo = `<p>Set the path to an EPUB file in <code>${esc(p.configPath)}</code>:</p>
                   <pre>book = "~/Books/my-book.epub"</pre><p>Save the file and the book opens right away.</p>`;
    if (!p.book) {
      if (p.error) showMessage("Couldn't open the book", `<p>${esc(p.error)}</p>${howTo}`, true);
      else showMessage("No book open", howTo, false);
      return;
    }
    $("message").className = "hidden";
    S.book = p.book;
    S.back = [];
    S.highlights = p.highlights || {};
    $("title").textContent = p.book.title;
    $("author").textContent = p.book.author ? "by " + p.book.author : "";
    S.L = computeLayout();
    applyLayout(S.L);
    const pos = p.position || {};
    loadChapter(pos.chapter || 0, { fraction: pos.fraction || 0 });
  }

  function settings(s) {
    S.settings = s;
    if (!S.book) return;
    S.L = computeLayout();
    applyLayout(S.L);
    loadChapter(S.chapter, { fraction: S.pages ? S.page / S.pages : 0 });  // re-applies fonts & hyphenation
  }

  // ── Wiring ───────────────────────────────────────────────────────────────
  document.addEventListener("keydown", onKeyDown);
  document.addEventListener("keyup", onKeyUp);
  window.addEventListener("blur", () => setSelectMode(false));
  document.addEventListener("wheel", onWheel, { passive: true });
  let resizeTimer = null;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(relayout, 150); });
  tickClock();
  setInterval(tickClock, 1000);

  return { open, settings, toast, hyphenated };
})();
