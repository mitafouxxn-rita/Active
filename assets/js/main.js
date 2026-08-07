/* ============================================================
   Vector Field — interaction layer
   Vanilla JS, no dependencies, no external assets.
   ============================================================ */
(() => {
  'use strict';

  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const lerp = (a, b, t) => a + (b - a) * t;
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  /* ── 1. preloader ──────────────────────────────────── */
  const loader = document.getElementById('loader');
  const loaderNum = document.getElementById('loaderNum');
  const loaderBar = document.getElementById('loaderBar');

  function runLoader(done) {
    let p = 0;
    const tick = () => {
      p = Math.min(100, p + Math.random() * 14 + 4);
      loaderNum.textContent = Math.round(p);
      loaderBar.style.width = p + '%';
      if (p < 100) return setTimeout(tick, 90 + Math.random() * 90);
      setTimeout(() => { loader.classList.add('is-done'); done(); }, 350);
    };
    tick();
  }

  /* ── 2. character split for big headlines ──────────── */
  function splitText() {
    document.querySelectorAll('[data-split]').forEach((el) => {
      const text = el.textContent;
      el.textContent = '';
      [...text].forEach((ch, i) => {
        const s = document.createElement('span');
        s.className = 'char';
        s.textContent = ch === ' ' ? ' ' : ch;
        s.style.transitionDelay = (i * 0.022).toFixed(3) + 's';
        el.appendChild(s);
      });
    });
  }

  /* ── 3. reveal on scroll ───────────────────────────── */
  function observeReveals() {
    const targets = document.querySelectorAll(
      '[data-reveal], .card, .hero__title, .contact__title'
    );
    if (!('IntersectionObserver' in window)) {
      targets.forEach((t) => t.classList.add('is-in'));
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (!e.isIntersecting) return;
          e.target.classList.add('is-in');
          io.unobserve(e.target);
        });
      },
      { threshold: 0.15, rootMargin: '0px 0px -8% 0px' }
    );
    targets.forEach((t) => io.observe(t));
  }

  /* ── 4. custom cursor ──────────────────────────────── */
  function initCursor() {
    const cursor = document.getElementById('cursor');
    if (!cursor || matchMedia('(pointer: coarse)').matches) return;

    const dot = cursor.querySelector('.cursor__dot');
    const ring = cursor.querySelector('.cursor__ring');
    const label = cursor.querySelector('.cursor__label');
    let mx = innerWidth / 2, my = innerHeight / 2;
    let rx = mx, ry = my;

    addEventListener('pointermove', (e) => {
      mx = e.clientX; my = e.clientY;
      dot.style.transform = `translate(${mx}px, ${my}px) translate(-50%,-50%)`;
    }, { passive: true });

    (function frame() {
      rx = lerp(rx, mx, 0.16);
      ry = lerp(ry, my, 0.16);
      ring.style.transform = `translate(${rx}px, ${ry}px) translate(-50%,-50%)`;
      requestAnimationFrame(frame);
    })();

    document.querySelectorAll('a, button, [data-cursor]').forEach((el) => {
      el.addEventListener('pointerenter', () => {
        cursor.classList.add('is-hot');
        label.textContent = el.dataset.cursor || '';
        if (!el.dataset.cursor) cursor.classList.remove('is-hot');
      });
      el.addEventListener('pointerleave', () => cursor.classList.remove('is-hot'));
    });
  }

  /* ── 5. nav: hide on scroll down, stick on scroll up ─ */
  function initNav() {
    const nav = document.getElementById('nav');
    const burger = document.getElementById('burger');
    const menu = document.getElementById('menu');
    let last = 0;

    addEventListener('scroll', () => {
      const y = scrollY;
      nav.classList.toggle('is-stuck', y > 40);
      if (!menu.classList.contains('is-open')) {
        nav.classList.toggle('is-hidden', y > last && y > 300);
      }
      last = y;
    }, { passive: true });

    const closeMenu = () => {
      menu.classList.remove('is-open');
      burger.setAttribute('aria-expanded', 'false');
      document.body.classList.remove('is-locked');
    };

    burger.addEventListener('click', () => {
      const open = menu.classList.toggle('is-open');
      burger.setAttribute('aria-expanded', String(open));
      document.body.classList.toggle('is-locked', open);
    });

    document.querySelectorAll('[data-nav]').forEach((a) => {
      a.addEventListener('click', (e) => {
        const target = document.querySelector(a.getAttribute('href'));
        if (!target) return;
        e.preventDefault();
        closeMenu();
        target.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' });
      });
    });

    addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && menu.classList.contains('is-open')) closeMenu();
    });
  }

  /* ── 6. marquee (scroll-reactive infinite scroller) ── */
  function initMarquee() {
    const track = document.getElementById('marquee');
    if (!track) return;

    // duplicate content until it comfortably overflows twice
    const original = track.innerHTML;
    let guard = 0;
    while (track.scrollWidth < innerWidth * 2 && guard++ < 8) track.innerHTML += original;

    const half = track.scrollWidth / 2;
    let x = 0, speed = 0.55, boost = 0, lastY = scrollY;

    addEventListener('scroll', () => {
      boost += (scrollY - lastY) * 0.06;
      lastY = scrollY;
    }, { passive: true });

    (function frame() {
      boost = lerp(boost, 0, 0.06);
      x -= speed + boost;
      if (x <= -half) x += half;
      if (x > 0) x -= half;
      track.style.transform = `translate3d(${x}px,0,0)`;
      requestAnimationFrame(frame);
    })();
  }

  /* ── 7. animated counters ──────────────────────────── */
  function initCounters() {
    const nums = document.querySelectorAll('[data-count]');
    if (!nums.length) return;
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (!e.isIntersecting) return;
        const el = e.target;
        const goal = parseInt(el.dataset.count, 10);
        const t0 = performance.now();
        const dur = 1400;
        (function step(t) {
          const k = clamp((t - t0) / dur, 0, 1);
          const eased = 1 - Math.pow(1 - k, 3);
          el.textContent = Math.round(goal * eased);
          if (k < 1) requestAnimationFrame(step);
        })(t0);
        io.unobserve(el);
      });
    }, { threshold: 0.6 });
    nums.forEach((n) => io.observe(n));
  }

  /* ── 8. procedural card thumbnails + pointer glare ─── */
  function initCards() {
    document.querySelectorAll('.card').forEach((card) => {
      const canvas = card.querySelector('canvas');
      const hue = Number(card.dataset.hue || 200);
      paintThumb(canvas, hue);

      card.addEventListener('pointermove', (e) => {
        const r = card.getBoundingClientRect();
        card.style.setProperty('--mx', ((e.clientX - r.left) / r.width) * 100 + '%');
        card.style.setProperty('--my', ((e.clientY - r.top) / r.height) * 100 + '%');
      });
    });

    let t;
    addEventListener('resize', () => {
      clearTimeout(t);
      t = setTimeout(() => {
        document.querySelectorAll('.card').forEach((c) =>
          paintThumb(c.querySelector('canvas'), Number(c.dataset.hue || 200))
        );
      }, 200);
    });
  }

  // A tiny value-noise field rasterised to a gradient — gives each project
  // a distinct, deterministic "cover art" without shipping any images.
  function paintThumb(canvas, hue) {
    const box = canvas.parentElement.getBoundingClientRect();
    const dpr = Math.min(devicePixelRatio || 1, 2);
    const w = Math.max(2, Math.round(box.width * dpr));
    const h = Math.max(2, Math.round(box.height * dpr));
    canvas.width = w; canvas.height = h;

    const ctx = canvas.getContext('2d');
    const g = ctx.createLinearGradient(0, 0, w, h);
    g.addColorStop(0, `hsl(${hue} 70% 8%)`);
    g.addColorStop(0.5, `hsl(${(hue + 28) % 360} 60% 16%)`);
    g.addColorStop(1, `hsl(${(hue + 300) % 360} 55% 6%)`);
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);

    // seeded rand so a thumbnail looks the same on every reload
    let seed = hue * 9781;
    const rand = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);

    // soft light blobs
    ctx.globalCompositeOperation = 'lighter';
    for (let i = 0; i < 14; i++) {
      const x = rand() * w, y = rand() * h, r = (0.12 + rand() * 0.35) * Math.min(w, h);
      const rg = ctx.createRadialGradient(x, y, 0, x, y, r);
      rg.addColorStop(0, `hsla(${(hue + rand() * 90) % 360} 90% 60% / .30)`);
      rg.addColorStop(1, 'hsla(0 0% 0% / 0)');
      ctx.fillStyle = rg;
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
    }

    // contour lines — reads as a topographic / field-line motif
    ctx.globalCompositeOperation = 'source-over';
    ctx.lineWidth = Math.max(1, dpr * 0.8);
    const rows = 26;
    for (let i = 0; i < rows; i++) {
      ctx.beginPath();
      ctx.strokeStyle = `hsla(${hue} 100% 80% / ${0.03 + (i % 5 === 0 ? 0.09 : 0.02)})`;
      const base = (i / rows) * h;
      for (let x = 0; x <= w; x += 8) {
        const n =
          Math.sin(x * 0.006 + i * 0.5) * 18 +
          Math.sin(x * 0.017 - i * 0.9) * 9 +
          Math.sin(x * 0.031 + i * 1.7) * 4;
        const y = base + n * dpr;
        x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.stroke();
    }

    // vignette
    const vg = ctx.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.25, w / 2, h / 2, Math.max(w, h) * 0.75);
    vg.addColorStop(0, 'rgba(0,0,0,0)');
    vg.addColorStop(1, 'rgba(0,0,0,.55)');
    ctx.fillStyle = vg;
    ctx.fillRect(0, 0, w, h);
  }

  /* ── 9. WebGL background field ─────────────────────── */
  const VERT = `
    attribute vec2 aPos;
    void main(){ gl_Position = vec4(aPos, 0.0, 1.0); }
  `;

  const FRAG = `
    precision highp float;
    uniform vec2  uRes;
    uniform float uTime;
    uniform vec2  uMouse;
    uniform float uScroll;

    // classic 2D value noise + fbm
    float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
    float noise(vec2 p){
      vec2 i = floor(p), f = fract(p);
      vec2 u = f * f * (3.0 - 2.0 * f);
      return mix(mix(hash(i), hash(i + vec2(1,0)), u.x),
                 mix(hash(i + vec2(0,1)), hash(i + vec2(1,1)), u.x), u.y);
    }
    float fbm(vec2 p){
      float v = 0.0, a = 0.5;
      for (int i = 0; i < 5; i++){
        v += a * noise(p);
        p = p * 2.02 + vec2(3.7, 1.3);
        a *= 0.5;
      }
      return v;
    }

    void main(){
      vec2 uv = (gl_FragCoord.xy - 0.5 * uRes) / min(uRes.x, uRes.y);
      float t = uTime * 0.045;

      // pointer pulls the field around
      vec2 m = (uMouse - 0.5) * 1.1;
      uv += m * 0.10;
      uv.y += uScroll * 0.30;

      // domain warp -> slow flowing plasma
      vec2 q = vec2(fbm(uv * 1.6 + t), fbm(uv * 1.6 + vec2(5.2, 1.3) - t));
      vec2 r = vec2(fbm(uv * 1.9 + 3.4 * q + vec2(1.7, 9.2) + t * 1.4),
                    fbm(uv * 1.9 + 3.4 * q + vec2(8.3, 2.8) - t * 1.1));
      float f = fbm(uv * 2.2 + 3.6 * r);

      vec3 deep   = vec3(0.027, 0.027, 0.035);
      vec3 violet = vec3(0.235, 0.196, 0.784);
      vec3 lime   = vec3(0.909, 1.000, 0.325);

      vec3 col = mix(deep, violet * 0.30, clamp(f * 1.4, 0.0, 1.0));
      col = mix(col, lime * 0.18, clamp(pow(r.x, 3.0) * 1.5, 0.0, 1.0));

      // field lines
      float lines = smoothstep(0.48, 0.5, fract(f * 8.0 + r.y * 2.0));
      col += lines * 0.022 * vec3(0.7, 0.8, 1.0);

      // glow around the pointer
      float d = length(uv - m * 0.9);
      col += exp(-d * 3.2) * 0.06 * violet;

      // keep the field well behind the type
      col *= 0.42;

      // vignette + subtle dithering to kill banding
      col *= 1.0 - 0.55 * length(uv) * 0.85;
      col += (hash(gl_FragCoord.xy + uTime) - 0.5) * 0.012;

      gl_FragColor = vec4(col, 1.0);
    }
  `;

  function initBackground() {
    const canvas = document.getElementById('bg');
    if (!canvas) return;
    const gl = canvas.getContext('webgl', { antialias: false, alpha: false, powerPreference: 'low-power' })
            || canvas.getContext('experimental-webgl');
    if (!gl) return; // CSS gradient fallback stays visible

    const compile = (type, src) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
        console.warn(gl.getShaderInfoLog(s));
        return null;
      }
      return s;
    };

    const vs = compile(gl.VERTEX_SHADER, VERT);
    const fs = compile(gl.FRAGMENT_SHADER, FRAG);
    if (!vs || !fs) return;

    const prog = gl.createProgram();
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return;
    gl.useProgram(prog);

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 3,-1, -1,3]), gl.STATIC_DRAW);
    const aPos = gl.getAttribLocation(prog, 'aPos');
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

    const uRes = gl.getUniformLocation(prog, 'uRes');
    const uTime = gl.getUniformLocation(prog, 'uTime');
    const uMouse = gl.getUniformLocation(prog, 'uMouse');
    const uScroll = gl.getUniformLocation(prog, 'uScroll');

    // render at reduced resolution — the field is soft, nobody can tell
    const scale = 0.6;
    function resize() {
      const dpr = Math.min(devicePixelRatio || 1, 1.5) * scale;
      canvas.width = Math.max(1, Math.round(innerWidth * dpr));
      canvas.height = Math.max(1, Math.round(innerHeight * dpr));
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.uniform2f(uRes, canvas.width, canvas.height);
    }
    resize();
    addEventListener('resize', resize);

    let tx = 0.5, ty = 0.5, mx = 0.5, my = 0.5, visible = true;
    addEventListener('pointermove', (e) => {
      tx = e.clientX / innerWidth;
      ty = 1 - e.clientY / innerHeight;
    }, { passive: true });
    document.addEventListener('visibilitychange', () => { visible = !document.hidden; });

    const t0 = performance.now();
    (function frame(now) {
      requestAnimationFrame(frame);
      if (!visible) return;
      mx = lerp(mx, tx, 0.05);
      my = lerp(my, ty, 0.05);
      const doc = Math.max(1, document.body.scrollHeight - innerHeight);
      gl.uniform1f(uTime, reduced ? 0 : (now - t0) / 1000);
      gl.uniform2f(uMouse, mx, my);
      gl.uniform1f(uScroll, scrollY / doc);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    })(t0);
  }

  /* ── 10. footer clock (JST) ────────────────────────── */
  function initClock() {
    const el = document.getElementById('clock');
    if (!el) return;
    const tick = () => {
      el.textContent = new Intl.DateTimeFormat('en-GB', {
        timeZone: 'Asia/Tokyo', hour: '2-digit', minute: '2-digit',
        second: '2-digit', hour12: false,
      }).format(new Date());
    };
    tick();
    setInterval(tick, 1000);
  }

  /* ── boot ──────────────────────────────────────────── */
  splitText();
  initCursor();
  initNav();
  initCards();
  initCounters();
  initClock();
  initBackground();

  addEventListener('load', () => {
    initMarquee();
    runLoader(() => {
      observeReveals();
      document.querySelector('.hero__title')?.classList.add('is-in');
      document.querySelectorAll('.hero__meta, .hero__sub').forEach((el) =>
        el.classList.add('is-in')
      );
    });
  });
})();
