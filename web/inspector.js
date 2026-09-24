// Replays traces recorded by `python -m jevis --demo --trace-out web/traces.js`.
// Everything shown comes from the trace: the voice envelope, the matcher's
// patterns, the plan, each UIA target's BoundingRectangle and tree, and the
// event timestamps. The only invented thing is pacing between stages.
(() => {
  const data = window.JEVIS_TRACES;
  const $ = (id) => document.getElementById(id);
  const FILLER = /^(?:hey |ok |okay |please |can you |could you |i want you to |jevis[,: ]*)+/i;
  // The minimap frames the union of every recorded target rect, padded and
  // kept at 16:9, so small targets such as a menu item stay visible.
  const SCREEN = (() => {
    let l = Infinity, t = Infinity, r = -Infinity, b = -Infinity;
    (data && data.traces || []).forEach((tr) => tr.events.forEach((e) => {
      if (e.kind === "target" && e.rect) {
        l = Math.min(l, e.rect[0]); t = Math.min(t, e.rect[1]);
        r = Math.max(r, e.rect[2]); b = Math.max(b, e.rect[3]);
      }
    }));
    if (!isFinite(l)) return { x: 0, y: 0, w: 1920, h: 1080 };
    let w = (r - l) * 1.16, h = (b - t) * 1.16;
    if (w / h > 16 / 9) h = w * 9 / 16; else w = h * 16 / 9;
    return { x: (l + r - w) / 2, y: (t + b - h) / 2, w, h };
  })();
  const MIN_GAP = 0.42;       // seconds between replayed agent events at 1x

  if (!data || !data.traces || !data.traces.length) {
    document.body.insertAdjacentHTML("beforeend", "<p style='color:#fb7185'>No traces found. Run the demo with --trace-out.</p>");
    return;
  }
  $("generated").textContent = `Recorded ${data.generated}.`;
  $("screen").querySelector(".screen-label").textContent =
    `screen px ${Math.round(SCREEN.x)},${Math.round(SCREEN.y)} to ${Math.round(SCREEN.x + SCREEN.w)},${Math.round(SCREEN.y + SCREEN.h)}`;

  let current = 0, timeline = [], clock = 0, last = 0, playing = false, speed = 1, raf = 0;
  let waveLevel = () => 0;
  const stages = [...document.querySelectorAll(".stage")];

  // --- tabs ----------------------------------------------------------------
  data.traces.forEach((trace, i) => {
    const b = document.createElement("button");
    b.innerHTML = `${esc(trace.instruction)}<small>${trace.elapsed.toFixed(1)}s</small>`;
    b.setAttribute("role", "tab");
    b.onclick = () => start(i);
    $("tabs").appendChild(b);
  });
  $("play").onclick = () => start(current);
  $("speed").onchange = (e) => { speed = parseFloat(e.target.value); };

  function esc(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  function stage(name, state) {
    stages.forEach((el) => {
      if (el.dataset.stage === name) {
        el.classList.toggle("active", state === "active");
        el.classList.toggle("passed", state === "passed");
      }
    });
  }

  function orb(colour) { $("orb").style.setProperty("--orb", colour); }

  // --- the voice ------------------------------------------------------------
  const canvas = $("wave"), ctx = canvas.getContext("2d");
  function drawWave(t) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const bars = Math.floor(w / 7), level = waveLevel();
    for (let i = 0; i < bars; i++) {
      const centre = 1 - Math.pow(Math.abs((i - (bars - 1) / 2) / (bars / 2)), 1.6);
      const wobble = 0.55 + 0.45 * Math.sin(t * 9 + i * 0.75) * Math.sin(t * 3.1 + i * 0.3);
      const bh = 2 + level * (h / 2 - 4) * (0.25 + 0.75 * centre) * wobble;
      const tone = Math.min(1, 0.18 + bh / 20);
      ctx.fillStyle = `rgba(91,157,255,${0.15 + 0.85 * tone * (level > 0.01 ? 1 : 0.3)})`;
      ctx.beginPath();
      ctx.roundRect(i * 7 + 2, h / 2 - bh, 3.5, bh * 2, 2);
      ctx.fill();
    }
  }

  // --- building one trace's timeline ----------------------------------------
  function start(index) {
    current = index;
    [...$("tabs").children].forEach((b, i) => b.setAttribute("aria-selected", String(i === index)));
    reset();
    timeline = build(data.traces[index]);
    clock = 0; last = performance.now(); playing = true;
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(tick);
  }

  function reset() {
    stages.forEach((el) => el.classList.remove("active", "passed"));
    $("typed").textContent = "";
    $("norm").innerHTML = "";
    $("tier").innerHTML = "";
    $("steps").innerHTML = "";
    $("result").className = "result";
    $("result").innerHTML = "";
    $("tree").innerHTML = "";
    $("log").innerHTML = "";
    $("screen").querySelectorAll(".rect").forEach((r) => r.remove());
    $("skills").innerHTML = data.skills.map((s) =>
      `<li><span>${esc(s.name)}</span><code>${esc(short(s.pattern))}</code><span class="why"></span></li>`).join("");
    waveLevel = () => 0;
    orb("#525b6d");
  }

  function short(pattern) {
    // The app alternation is long; collapse it so the shape of the rule shows.
    return pattern.replace(/\((?:my default browser|[^()]*\|[^()]*\|[^()]*\|[^()]*)\)/, "(APP)");
  }

  function build(trace) {
    const plan = [];
    const at = (t, fn) => plan.push({ t, fn, done: false });
    const voice = trace.voice || { fps: 30, levels: [] };
    const talk = Math.max(1.2, voice.levels.length / voice.fps);
    let t = 0.2;

    // 1-2. voice and transcript, together, the way dictation lands.
    at(t, () => { stage("voice", "active"); stage("transcript", "active"); orb("#5b9dff"); });
    const t0 = t;
    at(t, () => {
      waveLevel = () => {
        const i = Math.floor((clock - t0) * voice.fps);
        return i >= 0 && i < voice.levels.length ? voice.levels[i] : 0;
      };
    });
    const text = trace.instruction;
    for (let k = 1; k <= text.length; k++) {
      at(t0 + (talk * k) / text.length, () => { $("typed").textContent = text.slice(0, k); });
    }
    t = t0 + talk + 0.35;
    at(t, () => {
      stage("voice", "passed");
      const stripped = text.trim().replace(FILLER, "");
      $("norm").innerHTML = stripped === text.trim()
        ? `normalise() <b>${esc(trace.normalised)}</b> <span style="color:#525b6d">nothing to strip</span>`
        : `normalise() <b>${esc(trace.normalised)}</b>`;
    });

    // 3. the matcher, tried in order.
    t += 0.55;
    at(t, () => { stage("transcript", "passed"); stage("match", "active"); orb("#a78bfa"); });
    const rows = () => [...$("skills").children];
    let hit = -1;
    data.skills.forEach((s, i) => {
      const m = new RegExp(s.pattern, "i").exec(trace.normalised);
      if (hit === -1 && m && s.name === trace.skill) hit = i;
    });
    const tried = hit === -1 ? data.skills.length : hit + 1;
    for (let i = 0; i < tried; i++) {
      at(t + i * 0.26, () => rows()[i].classList.add("scan"));
      at(t + i * 0.26 + 0.2, () => {
        const row = rows()[i];
        row.classList.remove("scan");
        if (i === hit) {
          row.classList.add("hit");
          const m = new RegExp(data.skills[i].pattern, "i").exec(trace.normalised);
          const groups = m.slice(1).filter(Boolean).map((g) => `<b>${esc(g)}</b>`).join(" ");
          row.querySelector(".why").textContent = "match";
          $("tier").innerHTML = `<span class="pill">deterministic</span>${esc(trace.skill)}, no model call` +
            (groups ? `<div class="groups">captured ${groups}</div>` : "");
        } else {
          row.classList.add("miss");
        }
      });
    }
    t += tried * 0.26 + 0.35;
    if (hit === -1) {
      at(t, () => { $("tier").innerHTML = `<span class="pill" style="color:#a78bfa;border-color:#4c3d7a;background:#1c1733">model tier</span>${esc(trace.tier)}`; });
    }

    // 4. plan and the replayed agent events, at their real relative times.
    const events = trace.events.filter((e) => e.kind !== "result");
    const matchEvent = events.find((e) => e.kind === "match");
    at(t, () => {
      stage("match", "passed"); stage("plan", "active"); orb("#5b9dff");
      (matchEvent ? matchEvent.steps : []).forEach((label, i) => {
        const li = document.createElement("li");
        li.innerHTML = `<svg class="glyph" viewBox="0 0 18 18"><circle cx="9" cy="9" r="7"/><circle class="spin" cx="9" cy="9" r="7"/><path d="M5.5 9.5l2.5 2.5 4.5-5"/></svg>
          <div><div class="step-title"><code>${i + 1}</code>${esc(label)}</div>
          <div class="phases"><span class="phase uia">UIA query</span><span class="phase req">require</span>
          <span class="phase act">act</span><span class="phase exp">expect</span></div></div>`;
        $("steps").appendChild(li);
        setTimeout(() => li.classList.add("in"), 40 + i * 90);
      });
    });
    t += 0.6;
    let prevReal = 0, cursor = t;
    const stepEl = (i) => $("steps").children[i - 1];
    events.forEach((e) => {
      cursor += Math.max(MIN_GAP * (e.kind === "match" ? 0 : 1), e.t - prevReal);
      prevReal = e.t;
      at(cursor, () => apply(e, stepEl));
    });
    const result = trace.events.find((e) => e.kind === "result");
    cursor += 0.6;
    at(cursor, () => {
      stage("plan", "passed"); stage("done", "active");
      orb(trace.ok ? "#34d399" : "#fb7185");
      const r = $("result");
      r.className = "result " + (trace.ok ? "ok" : "fail");
      r.innerHTML = trace.ok
        ? `<big>Done</big>${esc(trace.skill || trace.tier)} in ${trace.elapsed.toFixed(2)}s, ${trace.attempts} attempt${trace.attempts > 1 ? "s" : ""}, every postcondition read back from the UI`
        : `<big>Failed</big>${esc(trace.error || "")}`;
      log({ t: result ? result.t : trace.elapsed, kind: "result", ok: trace.ok });
    });
    at(cursor + 3.2, () => {
      if (playing) start((current + 1) % data.traces.length);
    });
    return plan.sort((a, b) => a.t - b.t);
  }

  // --- replaying agent events -------------------------------------------------
  function apply(e, stepEl) {
    log(e);
    const li = e.index ? stepEl(e.index) : null;
    const phase = (cls, state) => {
      if (!li) return;
      const el = li.querySelector(".phase." + cls);
      el.classList.add("on");
      if (state) el.classList.add(state);
      return el;
    };
    if (e.kind === "step" && li) {
      li.classList.add("running");
    } else if (e.kind === "target") {
      phase("uia");
      phase("act");
      showTarget(e);
      tree(e);
    } else if (e.kind === "verify") {
      const el = phase(e.clause === "require" ? "req" : "exp", e.ok ? "good" : "bad");
      if (el) el.textContent = `${e.clause} ${e.ok ? "✓" : "✗"} ${e.detail}`;
      if (e.clause === "expect" && li) {
        li.classList.remove("running");
        li.classList.add(e.ok ? "ok" : "fail");
        const hot = $("screen").querySelector(".rect.hot");
        if (hot && e.ok) { hot.classList.remove("hot"); hot.classList.add("verified"); }
      }
    } else if (e.kind === "attempt") {
      if (li) li.classList.add("fail");
    }
  }

  function showTarget(e) {
    if (!e.rect) return;
    const screen = $("screen");
    screen.querySelectorAll(".rect.hot").forEach((r) => r.classList.remove("hot"));
    const [l, t, r, b] = e.rect;
    const box = document.createElement("div");
    box.className = "rect hot" + (e.role === "Window" ? " win" : "");
    Object.assign(box.style, {
      left: ((l - SCREEN.x) / SCREEN.w) * 100 + "%", top: ((t - SCREEN.y) / SCREEN.h) * 100 + "%",
      width: ((r - l) / SCREEN.w) * 100 + "%", height: ((b - t) / SCREEN.h) * 100 + "%",
    });
    box.innerHTML = `<span>${esc(e.role)} · ${esc(e.name || "")} [${l},${t},${r},${b}]</span>`;
    screen.appendChild(box);
  }

  function tree(e) {
    const lines = e.tree || [];
    $("tree-title").textContent = `${e.elements} elements, first ${Math.max(0, lines.length - 1)} shown`;
    const needle = e.role === "Window" ? null : `${e.role} "${e.name}"`;
    let marked = false;
    $("tree").innerHTML = lines.map((line, i) => {
      let cls = i === 0 ? "head" : "";
      if (!marked && ((needle && line.includes(needle)) || (!needle && i === 0))) { cls += " hl"; marked = true; }
      return `<span class="${cls.trim()}">${esc(line)}</span>`;
    }).join("\n");
  }

  function log(e) {
    const li = document.createElement("li");
    li.className = "ev-" + e.kind;
    const what = e.kind === "match" ? `${e.tier} ${e.skill || ""} [${(e.steps || []).length} steps]`
      : e.kind === "step" ? e.label
      : e.kind === "target" ? `${e.role} "${e.name}" (${e.elements} elements)`
      : e.kind === "verify" ? `${e.clause} ${e.ok ? "ok" : "FAILED"}: ${e.detail}`
      : e.kind === "result" ? (e.ok ? "success" : "failure")
      : JSON.stringify(e);
    li.innerHTML = `<span class="t">${Number(e.t).toFixed(3)}s</span><span class="k">${e.kind}</span><span>${esc(what)}</span>`;
    $("log").appendChild(li);
    $("log").scrollTop = $("log").scrollHeight;
  }

  // --- clock ------------------------------------------------------------------
  function tick(now) {
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (playing) clock += dt * speed;
    for (const item of timeline) {
      if (!item.done && item.t <= clock) { item.done = true; item.fn(); }
    }
    drawWave(now / 1000);
    raf = requestAnimationFrame(tick);
  }

  start(0);
})();
