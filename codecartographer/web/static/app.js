/* CodeCartographer dashboard.
 *
 * Reads either an embedded payload (window.__ANALYSIS__, used by the
 * standalone HTML report) or the live API. No frameworks and no CDN, so the
 * exported report opens from a file:// URL on a machine with no network —
 * which is the situation most legacy code actually lives in.
 */
(function () {
  "use strict";

  var DATA = null;
  var state = { tab: "map", smellFilter: "all", sort: { key: null, dir: -1 } };

  // ---------------------------------------------------------------- utils

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === "class") node.className = attrs[k];
        else if (k === "html") node.innerHTML = attrs[k];
        else if (k === "text") node.textContent = attrs[k];
        else if (k.slice(0, 2) === "on") node.addEventListener(k.slice(2), attrs[k]);
        else if (attrs[k] !== null && attrs[k] !== undefined)
          node.setAttribute(k, attrs[k]);
      });
    }
    (children || []).forEach(function (c) {
      if (c === null || c === undefined) return;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }

  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function num(n) {
    return (n === null || n === undefined) ? "—" : Number(n).toLocaleString();
  }

  function levelClass(level) {
    var l = String(level || "").toLowerCase();
    if (l === "critical") return "critical";
    if (l === "high") return "high";
    if (l === "moderate" || l === "medium") return "moderate";
    return "low";
  }

  function riskColor(score) {
    if (score >= 75) return "var(--critical)";
    if (score >= 55) return "var(--high)";
    if (score >= 35) return "var(--moderate)";
    return "var(--low)";
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  // ------------------------------------------------------------- sections

  function statCard(key, value, note) {
    return el("div", { class: "stat" }, [
      el("div", { class: "k", text: key }),
      el("div", { class: "v", text: value }),
      note ? el("div", { class: "n", text: note }) : null,
    ]);
  }

  function renderHeader() {
    var o = DATA.overview;
    var host = document.getElementById("hero");
    if (!host) return;
    clear(host);

    var langs = Object.keys(o.languages)
      .sort(function (a, b) { return o.languages[b].lines - o.languages[a].lines; });
    var langText = langs.slice(0, 4)
      .map(function (l) { return o.languages[l].label; }).join(", ");
    if (langs.length > 4) langText += " +" + (langs.length - 4) + " more";

    host.appendChild(el("h1", { text: o.root.split("/").slice(-2).join("/") }));
    host.appendChild(el("p", {
      text: num(o.files) + " files · " + num(o.total_lines) + " lines · " +
        langText + " · analysed in " + o.duration_seconds + "s",
    }));

    var cs = DATA.complexity_summary || {};
    var rs = DATA.risk_summary || {};
    var dead = (DATA.dead_code && DATA.dead_code.totals) || {};

    var stats = document.getElementById("stats");
    clear(stats);
    [
      ["Files", num(o.files), num(o.total_sloc) + " lines of code"],
      ["Functions", num(o.functions), num(o.types) + " types"],
      ["Maintainability", (cs.average_maintainability || 0) + "/100",
        (cs.files_below_threshold || 0) + " below threshold"],
      ["Average risk", (rs.average || 0), (rs.max || 0) + " highest"],
      ["Dependencies", num(o.dependencies), o.cycles + " cycle(s)"],
      ["Dead code", num(dead.unreachable || 0),
        "~" + num(dead.removable_lines || 0) + " lines removable"],
    ].forEach(function (row) {
      stats.appendChild(statCard(row[0], row[1], row[2]));
    });
  }

  // ---------------------------------------------------------------- map

  function renderMap() {
    var host = document.getElementById("panel-map");
    clear(host);

    var deps = DATA.dependencies || { nodes: [], edges: [] };
    if (!deps.nodes.length) {
      host.appendChild(el("div", { class: "empty", text: "No files to map." }));
      return;
    }

    var card = el("div", { class: "card" });
    card.appendChild(el("h2", { text: "Dependency map" }));
    card.appendChild(el("p", {
      class: "sub",
      text: "Each circle is a file, sized by how much depends on it and " +
        "coloured by change risk. An arrow means a resolved reference — a " +
        "call, an import, an inheritance or a declared type — not a name " +
        "that happened to appear in the text. Drag to rearrange, scroll to " +
        "zoom, click for detail.",
    }));

    var shell = el("div", { class: "map-shell" });
    var svgNS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("id", "map");
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");

    var controls = el("div", { class: "map-controls" }, [
      el("button", { class: "btn", text: "Re-layout", onclick: function () { layout(true); } }),
      el("button", { class: "btn", text: "Fit", onclick: function () { fit(); } }),
      el("button", {
        class: "btn", text: "Cycles only",
        onclick: function (e) {
          showCyclesOnly = !showCyclesOnly;
          e.target.setAttribute("aria-pressed", showCyclesOnly);
          draw();
        },
      }),
    ]);

    var legend = el("div", { class: "map-legend", html:
      '<i style="background:var(--critical)"></i>Critical risk<br>' +
      '<i style="background:var(--high)"></i>High<br>' +
      '<i style="background:var(--moderate)"></i>Moderate<br>' +
      '<i style="background:var(--low)"></i>Low' });
    var tip = el("div", { class: "map-tip" });

    shell.appendChild(svg);
    shell.appendChild(controls);
    shell.appendChild(legend);
    shell.appendChild(tip);
    card.appendChild(shell);
    host.appendChild(card);

    if (deps.cycles && deps.cycles.length) {
      var cycleCard = el("div", { class: "card" });
      cycleCard.appendChild(el("h2", { text: "Circular dependencies" }));
      cycleCard.appendChild(el("p", {
        class: "sub",
        text: "Groups of files that depend on each other in a loop. Nothing " +
          "in a loop can be understood, tested or deployed on its own.",
      }));
      deps.cycles.slice(0, 12).forEach(function (cycle) {
        cycleCard.appendChild(el("div", { class: "finding high" }, [
          el("h4", {}, [document.createTextNode(cycle.length + " files")]),
          el("p", { class: "path", text: cycle.join("  →  ") + "  →  " + cycle[0] }),
        ]));
      });
      host.appendChild(cycleCard);
    }

    // --- force-directed layout ------------------------------------------
    var W = 1000, H = 560;
    var nodes = deps.nodes.map(function (n) {
      return Object.assign({}, n, {
        x: W / 2 + (Math.random() - 0.5) * 400,
        y: H / 2 + (Math.random() - 0.5) * 300,
        vx: 0, vy: 0,
      });
    });
    var index = {};
    nodes.forEach(function (n, i) { index[n.id] = i; });
    var links = deps.edges
      .filter(function (e) { return index[e.from] !== undefined && index[e.to] !== undefined; })
      .map(function (e) { return { s: index[e.from], t: index[e.to], data: e }; });

    var showCyclesOnly = false;
    var view = { x: 0, y: 0, k: 1 };
    var dragNode = null, panning = false, last = null;

    function radius(n) {
      return 6 + Math.sqrt(n.in_degree * 9 + n.functions * 0.4);
    }

    function layout(reset) {
      if (reset) {
        nodes.forEach(function (n) {
          n.x = W / 2 + (Math.random() - 0.5) * W * 0.6;
          n.y = H / 2 + (Math.random() - 0.5) * H * 0.6;
        });
      }
      // Fruchterman-Reingold with the frame clamp the original paper
      // specifies. Without the clamp, disconnected components repel each
      // other forever and the graph ends up spanning tens of thousands of
      // units, which renders as a handful of specks.
      var n = nodes.length;
      var k = Math.sqrt((W * H) / Math.max(1, n));
      var temperature = W / 8;
      var iterations = 300;
      var cooling = Math.pow(0.6 / temperature, 1 / iterations);

      for (var step = 0; step < iterations; step++) {
        for (var i = 0; i < n; i++) { nodes[i].vx = 0; nodes[i].vy = 0; }

        for (var a = 0; a < n; a++) {
          for (var b = a + 1; b < n; b++) {
            var na = nodes[a], nb = nodes[b];
            var dx = na.x - nb.x, dy = na.y - nb.y;
            var dist = Math.sqrt(dx * dx + dy * dy);
            if (dist < 0.01) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; dist = 0.01; }
            var rep = (k * k) / dist;
            var ux = (dx / dist) * rep, uy = (dy / dist) * rep;
            na.vx += ux; na.vy += uy;
            nb.vx -= ux; nb.vy -= uy;
          }
        }

        links.forEach(function (l) {
          var na = nodes[l.s], nb = nodes[l.t];
          var dx = na.x - nb.x, dy = na.y - nb.y;
          var dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
          var att = (dist * dist) / k;
          var ux = (dx / dist) * att, uy = (dy / dist) * att;
          na.vx -= ux; na.vy -= uy;
          nb.vx += ux; nb.vy += uy;
        });

        // A little gravity, so components with no edges between them settle
        // near the middle instead of pinning themselves to the corners.
        for (var g = 0; g < n; g++) {
          nodes[g].vx += (W / 2 - nodes[g].x) * 0.03;
          nodes[g].vy += (H / 2 - nodes[g].y) * 0.03;
        }

        for (var m = 0; m < n; m++) {
          var node = nodes[m];
          var speed = Math.sqrt(node.vx * node.vx + node.vy * node.vy) || 1;
          var limit = Math.min(speed, temperature);
          node.x += (node.vx / speed) * limit;
          node.y += (node.vy / speed) * limit;
          node.x = Math.min(W, Math.max(0, node.x));
          node.y = Math.min(H, Math.max(0, node.y));
        }
        temperature *= cooling;
      }
      fit();
    }

    function fit() {
      var pad = 40 + Math.max.apply(null, nodes.map(radius));
      var xs = nodes.map(function (n) { return n.x; });
      var ys = nodes.map(function (n) { return n.y; });
      var minX = Math.min.apply(null, xs) - pad;
      var maxX = Math.max.apply(null, xs) + pad;
      var minY = Math.min.apply(null, ys) - pad;
      var maxY = Math.max.apply(null, ys) + pad;
      // Match the real rendered aspect ratio so circles stay circular and the
      // graph fills the panel instead of being letterboxed inside it.
      var w = Math.max(320, maxX - minX);
      var h = Math.max(220, maxY - minY);
      var box = svg.getBoundingClientRect();
      var target = (box.width && box.height) ? box.width / box.height : W / H;
      if (w / h > target) { h = w / target; } else { w = h * target; }
      var cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
      view = { x: cx - w / 2, y: cy - h / 2, w: w, h: h };
      svg.setAttribute("viewBox", view.x + " " + view.y + " " + w + " " + h);
      draw();
    }

    function visible(n) {
      return !showCyclesOnly || n.in_cycle;
    }

    function draw() {
      clear(svg);
      var defs = document.createElementNS(svgNS, "defs");
      defs.innerHTML =
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" ' +
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">' +
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="var(--border-strong)"/></marker>';
      svg.appendChild(defs);

      var gLinks = document.createElementNS(svgNS, "g");
      links.forEach(function (l) {
        var a = nodes[l.s], b = nodes[l.t];
        if (!visible(a) || !visible(b)) return;
        var line = document.createElementNS(svgNS, "line");
        var dx = b.x - a.x, dy = b.y - a.y;
        var d = Math.sqrt(dx * dx + dy * dy) || 1;
        var rb = radius(b) + 5;
        line.setAttribute("x1", a.x);
        line.setAttribute("y1", a.y);
        line.setAttribute("x2", b.x - (dx / d) * rb);
        line.setAttribute("y2", b.y - (dy / d) * rb);
        line.setAttribute("stroke", "var(--border-strong)");
        line.setAttribute("stroke-width", Math.min(3, 0.6 + (l.data.weight || 1) * 0.12));
        line.setAttribute("opacity", l.data.confidence === "exact" ? 0.75 : 0.4);
        line.setAttribute("marker-end", "url(#arrow)");
        gLinks.appendChild(line);
      });
      svg.appendChild(gLinks);

      var gNodes = document.createElementNS(svgNS, "g");
      nodes.forEach(function (n, i) {
        if (!visible(n)) return;
        var g = document.createElementNS(svgNS, "g");
        g.setAttribute("transform", "translate(" + n.x + "," + n.y + ")");
        g.style.cursor = "pointer";

        var c = document.createElementNS(svgNS, "circle");
        c.setAttribute("r", radius(n));
        c.setAttribute("fill", riskColor(n.risk));
        c.setAttribute("fill-opacity", "0.85");
        c.setAttribute("stroke", n.in_cycle ? "var(--critical)" : "var(--surface)");
        c.setAttribute("stroke-width", n.in_cycle ? 2.5 : 1.5);
        g.appendChild(c);

        var t = document.createElementNS(svgNS, "text");
        t.setAttribute("y", radius(n) + 13);
        t.setAttribute("text-anchor", "middle");
        t.setAttribute("font-size", "11");
        t.setAttribute("fill", "var(--text-dim)");
        t.setAttribute("font-family", "var(--mono)");
        t.textContent = n.label.length > 22 ? n.label.slice(0, 20) + "…" : n.label;
        g.appendChild(t);

        g.addEventListener("mousemove", function (ev) {
          tip.className = "map-tip show";
          tip.innerHTML =
            "<b>" + esc(n.id) + "</b>" +
            n.language + " · " + num(n.lines) + " lines · " +
            n.functions + " functions<br>" +
            "Depended on by " + n.in_degree + ", depends on " + n.out_degree +
            "<br>Risk " + n.risk + " (" + n.risk_level + ")" +
            (n.in_cycle ? "<br><b style='color:var(--critical)'>in a dependency cycle</b>" : "");
          var box = shell.getBoundingClientRect();
          tip.style.left = Math.min(ev.clientX - box.left + 14, box.width - 300) + "px";
          tip.style.top = (ev.clientY - box.top + 14) + "px";
        });
        g.addEventListener("mouseleave", function () { tip.className = "map-tip"; });
        g.addEventListener("click", function () { openFile(n.id); });
        g.addEventListener("mousedown", function (ev) {
          dragNode = i; last = { x: ev.clientX, y: ev.clientY }; ev.stopPropagation();
        });
        gNodes.appendChild(g);
      });
      svg.appendChild(gNodes);
    }

    svg.addEventListener("mousedown", function (ev) {
      panning = true; last = { x: ev.clientX, y: ev.clientY };
      svg.classList.add("dragging");
    });
    window.addEventListener("mouseup", function () {
      dragNode = null; panning = false; svg.classList.remove("dragging");
    });
    window.addEventListener("mousemove", function (ev) {
      if (dragNode === null && !panning) return;
      var box = svg.getBoundingClientRect();
      var scale = view.w / box.width;
      var dx = (ev.clientX - last.x) * scale;
      var dy = (ev.clientY - last.y) * scale;
      last = { x: ev.clientX, y: ev.clientY };
      if (dragNode !== null) {
        nodes[dragNode].x += dx;
        nodes[dragNode].y += dy;
        draw();
      } else {
        view.x -= dx; view.y -= dy;
        svg.setAttribute("viewBox", view.x + " " + view.y + " " + view.w + " " + view.h);
      }
    });
    svg.addEventListener("wheel", function (ev) {
      ev.preventDefault();
      var factor = ev.deltaY > 0 ? 1.12 : 0.89;
      var cx = view.x + view.w / 2, cy = view.y + view.h / 2;
      view.w *= factor; view.h *= factor;
      view.x = cx - view.w / 2; view.y = cy - view.h / 2;
      svg.setAttribute("viewBox", view.x + " " + view.y + " " + view.w + " " + view.h);
    }, { passive: false });

    layout(false);
  }

  // ---------------------------------------------------------------- risk

  function renderRisk() {
    var host = document.getElementById("panel-risk");
    clear(host);
    var entries = Object.keys(DATA.risk).map(function (k) { return DATA.risk[k]; });
    if (!entries.length) {
      host.appendChild(el("div", { class: "empty", text: "No risk data." }));
      return;
    }

    var rs = DATA.risk_summary || {};
    var summary = el("div", { class: "card" });
    summary.appendChild(el("h2", { text: "Change risk" }));
    summary.appendChild(el("p", {
      class: "sub",
      text: "A weighted score combining internal complexity, how many files " +
        "depend on this one, how often it changes, problems already found, " +
        "whether anything tests it, and how concentrated its knowledge is. " +
        "Every factor is shown so you can disagree with it.",
    }));

    var row = el("div", { class: "chart-row" });
    row.appendChild(barChart("Risk level", rs.distribution || {}, function (k) {
      return "var(--" + levelClass(k) + ")";
    }));
    row.appendChild(barChart("Complexity vs. churn", rs.quadrants || {}, function (k) {
      return k === "refactor-first" ? "var(--critical)"
        : k === "stable-but-complex" ? "var(--moderate)"
        : k === "healthy-churn" ? "var(--accent)" : "var(--neutral)";
    }, {
      "refactor-first": "Refactor first",
      "stable-but-complex": "Complex, stable",
      "healthy-churn": "Simple, active",
      "quiet": "Quiet",
    }));
    summary.appendChild(row);
    host.appendChild(summary);

    var card = el("div", { class: "card" });
    card.appendChild(el("h2", { text: "Every file, ranked" }));
    var scroll = el("div", { class: "table-scroll" });
    scroll.appendChild(riskTable(entries));
    card.appendChild(scroll);
    host.appendChild(card);

    var detail = el("div", { class: "card" });
    detail.appendChild(el("h2", { text: "Why the top file scores what it does" }));
    var top = entries.slice().sort(function (a, b) { return b.score - a.score; })[0];
    detail.appendChild(el("p", { class: "sub", text: top.narrative }));
    Object.keys(top.factors).forEach(function (key) {
      var f = top.factors[key];
      detail.appendChild(el("div", { class: "factor" }, [
        el("div", { class: "factor-head" }, [
          el("b", { text: f.label + (f.measured ? "" : " (assumed)") }),
          el("span", { text: f.normalised + " × " + f.weight + " = " + f.contribution }),
        ]),
        el("div", { class: "meter" }, [
          el("span", {
            style: "width:" + f.normalised + "%;background:" + riskColor(f.normalised),
          }),
        ]),
        el("div", { class: "factor-detail", text: f.detail }),
      ]));
    });
    host.appendChild(detail);
  }

  function riskTable(entries) {
    var cols = [
      { key: "file", label: "File", cls: "path" },
      { key: "score", label: "Risk", cls: "num" },
      { key: "level", label: "Level" },
      { key: "_cc", label: "Complexity", cls: "num" },
      { key: "_mi", label: "Maint.", cls: "num" },
      { key: "_commits", label: "Commits", cls: "num" },
      { key: "quadrant", label: "Quadrant" },
    ];
    entries.forEach(function (e) {
      var c = DATA.complexity[e.file] || {};
      e._cc = c.cyclomatic_total || 0;
      e._mi = c.maintainability_index || 0;
      e._commits = (e.git && e.git.commits) || 0;
    });

    var table = el("table");
    var thead = el("thead");
    var tr = el("tr");
    cols.forEach(function (col) {
      tr.appendChild(el("th", {
        class: "sortable " + (col.cls === "num" ? "num" : ""),
        text: col.label,
        onclick: function () {
          state.sort = {
            key: col.key,
            dir: state.sort.key === col.key ? -state.sort.dir : -1,
          };
          renderRisk();
        },
      }));
    });
    thead.appendChild(tr);
    table.appendChild(thead);

    var sortKey = state.sort.key || "score";
    var dir = state.sort.dir;
    entries.sort(function (a, b) {
      var x = a[sortKey], y = b[sortKey];
      // Numbers default to worst-first, which is the only ordering anyone
      // wants from a risk table; text defaults to A-Z.
      if (typeof x === "string") return -dir * x.localeCompare(y);
      return dir * ((x || 0) - (y || 0));
    });

    var tbody = el("tbody");
    entries.forEach(function (e) {
      tbody.appendChild(el("tr", {}, [
        el("td", { class: "path" }, [
          el("a", { href: "#", text: e.file, onclick: function (ev) {
            ev.preventDefault(); openFile(e.file);
          } }),
        ]),
        el("td", { class: "num", text: e.score }),
        el("td", {}, [el("span", {
          class: "badge " + levelClass(e.level), text: e.level,
        })]),
        el("td", { class: "num", text: num(e._cc) }),
        el("td", { class: "num", text: e._mi }),
        el("td", { class: "num", text: e._commits ? num(e._commits) : "—" }),
        el("td", {}, [el("span", {
          class: "badge " + (e.quadrant === "refactor-first" ? "critical" : "neutral"),
          text: (e.quadrant || "").replace(/-/g, " "),
        })]),
      ]));
    });
    table.appendChild(tbody);
    return table;
  }

  // ------------------------------------------------------------ dead code

  function renderDead() {
    var host = document.getElementById("panel-dead");
    clear(host);
    var dead = DATA.dead_code || {};
    var totals = dead.totals || {};

    var card = el("div", { class: "card" });
    card.appendChild(el("h2", { text: "Code nothing reaches" }));
    card.appendChild(el("p", {
      class: "sub",
      text: "Found by walking the resolved call graph out from every entry " +
        "point, not by searching for the method name. A function only " +
        "appears here if no path leads to it.",
    }));
    if (dead.mode_note) {
      card.appendChild(el("div", { class: "note", text: dead.mode_note }));
    }
    if ((dead.reflection_warnings || []).length) {
      card.appendChild(el("div", {
        class: "note warn",
        text: "This project uses runtime dispatch (" +
          dead.reflection_warnings.map(function (w) { return w.pattern; })
            .slice(0, 3).join(", ") +
          "), so a method can be invoked from a string at runtime. Confidence " +
          "below is reduced accordingly — verify before deleting anything.",
      }));
    }

    var stats = el("div", { class: "stats" });
    [
      ["Unreachable", num(totals.unreachable || 0), "no path from an entry point"],
      ["High confidence", num(dead.confident_findings || 0), "safe to review first"],
      ["Removable lines", num(totals.removable_lines || 0), "if all confirmed"],
      ["Unused imports", num(totals.unused_imports || 0), ""],
      ["Unused fields", num(totals.unused_fields || 0), ""],
      ["Dead files", num(totals.dead_files || 0), "nothing references them"],
    ].forEach(function (r) { stats.appendChild(statCard(r[0], r[1], r[2])); });
    card.appendChild(stats);
    host.appendChild(card);

    var list = (dead.unreachable || []);
    if (!list.length) {
      host.appendChild(el("div", { class: "empty", text: "Nothing unreachable found." }));
      return;
    }

    var table = el("div", { class: "card" });
    table.appendChild(el("h2", { text: "Unreachable functions" }));
    var scroll = el("div", { class: "table-scroll" });
    var t = el("table", {}, [
      el("thead", {}, [el("tr", {}, [
        el("th", { text: "Confidence" }),
        el("th", { text: "Function" }),
        el("th", { text: "File" }),
        el("th", { class: "num", text: "Line" }),
        el("th", { class: "num", text: "Lines" }),
        el("th", { text: "What could make this wrong" }),
      ])]),
    ]);
    var tbody = el("tbody");
    list.slice(0, 300).forEach(function (f) {
      tbody.appendChild(el("tr", {}, [
        el("td", {}, [el("span", {
          class: "badge " + (f.confidence === "high" ? "low"
            : f.confidence === "medium" ? "moderate" : "neutral"),
          text: f.confidence,
        })]),
        el("td", { class: "path", text: f.name }),
        el("td", { class: "path" }, [
          el("a", { href: "#", text: f.file, onclick: function (ev) {
            ev.preventDefault(); openFile(f.file, f.line);
          } }),
        ]),
        el("td", { class: "num", text: f.line }),
        el("td", { class: "num", text: f.loc }),
        el("td", { text: f.caveats.length ? f.caveats.join(" ") : "Nothing — no caller found anywhere." }),
      ]));
    });
    t.appendChild(tbody);
    scroll.appendChild(t);
    table.appendChild(scroll);
    host.appendChild(table);

    if ((dead.unused_imports || []).length) {
      var imports = el("div", { class: "card" });
      imports.appendChild(el("h2", { text: "Unused imports" }));
      var it = el("table", {}, [el("thead", {}, [el("tr", {}, [
        el("th", { text: "File" }), el("th", { class: "num", text: "Line" }),
        el("th", { text: "Statement" }),
      ])])]);
      var ib = el("tbody");
      dead.unused_imports.slice(0, 120).forEach(function (i) {
        ib.appendChild(el("tr", {}, [
          el("td", { class: "path", text: i.file }),
          el("td", { class: "num", text: i.line }),
          el("td", { class: "path", text: i.statement }),
        ]));
      });
      it.appendChild(ib);
      imports.appendChild(el("div", { class: "table-scroll" }, [it]));
      host.appendChild(imports);
    }
  }

  // --------------------------------------------------------------- smells

  function renderSmells() {
    var host = document.getElementById("panel-smells");
    clear(host);
    var smells = DATA.smells || [];

    var card = el("div", { class: "card" });
    card.appendChild(el("h2", { text: "Design problems" }));
    card.appendChild(el("p", {
      class: "sub",
      text: "Each finding names the measurement that triggered it and what " +
        "to do about it. Thresholds are deliberately conservative — a tool " +
        "that flags every 30-line method teaches people to ignore it.",
    }));

    var counts = {};
    smells.forEach(function (s) { counts[s.rule] = (counts[s.rule] || 0) + 1; });
    var pills = el("div", { class: "pill-row" });
    pills.appendChild(el("button", {
      class: "pill", "aria-pressed": state.smellFilter === "all",
      text: "All (" + smells.length + ")",
      onclick: function () { state.smellFilter = "all"; renderSmells(); },
    }));
    Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; })
      .forEach(function (rule) {
        var label = (smells.filter(function (s) { return s.rule === rule; })[0] || {}).title;
        pills.appendChild(el("button", {
          class: "pill", "aria-pressed": state.smellFilter === rule,
          text: label + " (" + counts[rule] + ")",
          onclick: function () { state.smellFilter = rule; renderSmells(); },
        }));
      });
    card.appendChild(pills);

    var shown = smells.filter(function (s) {
      return state.smellFilter === "all" || s.rule === state.smellFilter;
    });
    if (!shown.length) {
      card.appendChild(el("div", { class: "empty", text: "Nothing found." }));
    }
    shown.slice(0, 200).forEach(function (s) {
      card.appendChild(el("div", { class: "finding " + s.severity }, [
        el("h4", {}, [
          document.createTextNode(s.title),
          el("span", { class: "badge " + levelClass(s.severity), text: s.severity }),
          el("span", { class: "where", text: s.file + ":" + s.line }),
        ]),
        el("p", { text: s.detail }),
        el("p", { class: "action", text: s.action }),
      ]));
    });
    host.appendChild(card);
  }

  // ------------------------------------------------------------- business

  function renderBusiness() {
    var host = document.getElementById("panel-business");
    clear(host);
    var b = DATA.business || {};

    var card = el("div", { class: "card" });
    card.appendChild(el("h2", { text: "Business rules recovered from the code" }));
    card.appendChild(el("p", {
      class: "sub",
      text: "Thresholds, formulas, fixed constants and state transitions that " +
        "carry commercial meaning. These are the decisions the business made " +
        "years ago and then forgot it had written down here.",
    }));

    if (!b.total) {
      card.appendChild(el("div", { class: "empty", text: "No business rules identified." }));
      host.appendChild(card);
      return;
    }

    var scroll = el("div", { class: "table-scroll" });
    var t = el("table", {}, [el("thead", {}, [el("tr", {}, [
      el("th", { text: "Kind" }), el("th", { text: "Rule" }),
      el("th", { text: "Code" }), el("th", { text: "Where" }),
    ])])]);
    var tbody = el("tbody");
    (b.highlights || []).forEach(function (r) {
      tbody.appendChild(el("tr", {}, [
        el("td", {}, [el("span", { class: "badge neutral", text: r.kind })]),
        el("td", { text: r.explanation }),
        el("td", { class: "path", text: r.code }),
        el("td", { class: "path" }, [
          el("a", { href: "#", text: r.file + ":" + r.line, onclick: function (ev) {
            ev.preventDefault(); openFile(r.file, r.line);
          } }),
        ]),
      ]));
    });
    t.appendChild(tbody);
    scroll.appendChild(t);
    card.appendChild(scroll);
    host.appendChild(card);

    if ((DATA.glossary || []).length) {
      var g = el("div", { class: "card" });
      g.appendChild(el("h2", { text: "Domain vocabulary" }));
      g.appendChild(el("p", {
        class: "sub",
        text: "The concepts this system actually operates on, ranked by how " +
          "often they drive a decision. A useful starting point for a " +
          "glossary nobody ever wrote.",
      }));
      var gt = el("table", {}, [el("thead", {}, [el("tr", {}, [
        el("th", { text: "Term" }), el("th", { class: "num", text: "Rules" }),
        el("th", { class: "num", text: "Files" }), el("th", { text: "Example" }),
      ])])]);
      var gb = el("tbody");
      DATA.glossary.slice(0, 30).forEach(function (term) {
        gb.appendChild(el("tr", {}, [
          el("td", { text: term.term }),
          el("td", { class: "num", text: term.occurrences }),
          el("td", { class: "num", text: term.file_count }),
          el("td", { text: term.example }),
        ]));
      });
      gt.appendChild(gb);
      g.appendChild(el("div", { class: "table-scroll" }, [gt]));
      host.appendChild(g);
    }
  }

  // ---------------------------------------------------------------- files

  function renderFiles() {
    var host = document.getElementById("panel-files");
    clear(host);

    var card = el("div", { class: "card" });
    card.appendChild(el("h2", { text: "Every file" }));

    var langs = DATA.overview.languages;
    var row = el("div", { class: "chart-row" });
    var byLines = {};
    Object.keys(langs).forEach(function (k) { byLines[langs[k].label] = langs[k].lines; });
    row.appendChild(barChart("Lines by language", byLines, function () { return "var(--accent)"; }));

    var dist = DATA.classification_distribution || {};
    row.appendChild(barChart("What functions do", dist, function () {
      return "var(--moderate)";
    }));
    card.appendChild(row);

    if (DATA.model_card) {
      card.appendChild(el("div", {
        class: "note",
        text: "Function purpose is predicted by " + DATA.model_card.algorithm +
          ", measured " +
          Math.round(DATA.model_card.cross_validated_accuracy * 100) +
          "% accurate under " + DATA.model_card.validation + ". " +
          DATA.model_card.caveat,
      }));
    }

    var scroll = el("div", { class: "table-scroll" });
    var t = el("table", {}, [el("thead", {}, [el("tr", {}, [
      el("th", { text: "File" }), el("th", { text: "Language" }),
      el("th", { class: "num", text: "Lines" }), el("th", { class: "num", text: "Functions" }),
      el("th", { class: "num", text: "Maint." }), el("th", { text: "Risk" }),
      el("th", { class: "num", text: "Issues" }),
    ])])]);
    var tbody = el("tbody");
    (DATA.files || []).forEach(function (f) {
      var c = DATA.complexity[f.rel_path] || {};
      var r = DATA.risk[f.rel_path] || {};
      tbody.appendChild(el("tr", {}, [
        el("td", { class: "path" }, [
          el("a", { href: "#", text: f.rel_path, onclick: function (ev) {
            ev.preventDefault(); openFile(f.rel_path);
          } }),
        ]),
        el("td", { text: f.language }),
        el("td", { class: "num", text: num(f.lines) }),
        el("td", { class: "num", text: f.functions.length }),
        el("td", { class: "num", text: c.maintainability_index || "—" }),
        el("td", {}, [el("span", {
          class: "badge " + levelClass(r.level), text: (r.level || "—") + " " + (r.score || ""),
        })]),
        el("td", { class: "num", text: f.diagnostics.length }),
      ]));
    });
    t.appendChild(tbody);
    scroll.appendChild(t);
    card.appendChild(scroll);
    host.appendChild(card);
  }

  // -------------------------------------------------------------- quality

  function renderQuality() {
    var host = document.getElementById("panel-quality");
    clear(host);

    var card = el("div", { class: "card" });
    card.appendChild(el("h2", { text: "How much of this to trust" }));
    card.appendChild(el("p", {
      class: "sub",
      text: "Static analysis of unfamiliar code is always partial. These are " +
        "the numbers that tell you where the analysis is solid and where it " +
        "is guessing.",
    }));

    var r = DATA.resolution || {};
    var stats = el("div", { class: "stats" });
    [
      ["Call resolution", Math.round((r.resolution_rate || 0) * 100) + "%",
        r.resolved + " of " + r.internal_call_sites + " internal calls"],
      ["Proven exactly", num(r.exact || 0), "receiver type known"],
      ["By unique name", num(r.likely || 0), "only one candidate"],
      ["Ambiguous", num(r.ambiguous || 0), "several candidates kept"],
      ["Library calls", num(r.external_calls || 0), "target outside this project"],
      ["Parse failures", num(DATA.overview.files_with_diagnostics || 0),
        "files with structural problems"],
    ].forEach(function (x) { stats.appendChild(statCard(x[0], x[1], x[2])); });
    card.appendChild(stats);
    if (r.note) card.appendChild(el("div", { class: "note", text: r.note }));
    host.appendChild(card);

    var diagnostics = [];
    (DATA.files || []).forEach(function (f) {
      f.diagnostics.forEach(function (d) {
        diagnostics.push(Object.assign({ file: f.rel_path }, d));
      });
    });
    if (diagnostics.length) {
      var dcard = el("div", { class: "card" });
      dcard.appendChild(el("h2", { text: "Structural problems in the source" }));
      dcard.appendChild(el("p", {
        class: "sub",
        text: "Detected on a token stream with comments and string literals " +
          "removed, so a brace inside a string is not reported as an error.",
      }));
      var t = el("table", {}, [el("thead", {}, [el("tr", {}, [
        el("th", { text: "File" }), el("th", { class: "num", text: "Line" }),
        el("th", { text: "Problem" }), el("th", { text: "Source" }),
      ])])]);
      var tb = el("tbody");
      diagnostics.slice(0, 200).forEach(function (d) {
        tb.appendChild(el("tr", {}, [
          el("td", { class: "path", text: d.file }),
          el("td", { class: "num", text: d.line }),
          el("td", { text: d.message }),
          el("td", { class: "path", text: d.snippet }),
        ]));
      });
      t.appendChild(tb);
      dcard.appendChild(el("div", { class: "table-scroll" }, [t]));
      host.appendChild(dcard);
    }

    var langCard = el("div", { class: "card" });
    langCard.appendChild(el("h2", { text: "Languages this build understands" }));
    var lt = el("table", {}, [el("thead", {}, [el("tr", {}, [
      el("th", { text: "Language" }), el("th", { text: "Family" }),
      el("th", { text: "Extensions" }), el("th", { class: "num", text: "Found here" }),
    ])])]);
    var lb = el("tbody");
    (DATA.languages_supported || []).forEach(function (l) {
      var here = DATA.overview.languages[l.name];
      lb.appendChild(el("tr", {}, [
        el("td", { text: l.label }),
        el("td", { text: l.family }),
        el("td", { class: "path", text: l.extensions.join(" ") }),
        el("td", { class: "num", text: here ? here.files : "—" }),
      ]));
    });
    lt.appendChild(lb);
    langCard.appendChild(el("div", { class: "table-scroll" }, [lt]));
    host.appendChild(langCard);
  }

  // ---------------------------------------------------------------- chart

  function barChart(title, data, colorFn, labels) {
    var keys = Object.keys(data).sort(function (a, b) { return data[b] - data[a]; });
    var max = Math.max.apply(null, keys.map(function (k) { return data[k]; }).concat([1]));
    var wrap = el("div", {}, [el("h2", {
      text: title, style: "font-size:14px;margin:0 0 12px",
    })]);
    var bars = el("div", { class: "bars" });
    keys.forEach(function (k) {
      bars.appendChild(el("div", { class: "bar-row" }, [
        el("div", { class: "label", text: (labels && labels[k]) || k.replace(/_/g, " ") }),
        el("div", { class: "meter" }, [
          el("span", {
            style: "width:" + (data[k] / max * 100) + "%;background:" + colorFn(k),
          }),
        ]),
        el("div", { class: "num", text: num(data[k]) }),
      ]));
    });
    wrap.appendChild(bars);
    return wrap;
  }

  // ----------------------------------------------------------- file view

  function openFile(relPath, line) {
    if (window.__ANALYSIS__) {
      // Standalone report: no server to ask, so show what we already have.
      state.tab = "files";
      selectTab("files");
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    var url = "file?path=" + encodeURIComponent(relPath) +
      (window.__RUN__ ? "&run=" + encodeURIComponent(window.__RUN__) : "") +
      (line ? "#L" + line : "");
    window.location.href = url;
  }

  // ------------------------------------------------------------ chrome

  function selectTab(name) {
    state.tab = name;
    document.querySelectorAll(".tab").forEach(function (t) {
      t.setAttribute("aria-selected", t.dataset.tab === name);
    });
    document.querySelectorAll(".panel").forEach(function (p) {
      p.classList.toggle("active", p.id === "panel-" + name);
    });
    if (name === "map" && !document.querySelector("#map")) renderMap();
  }

  function renderAll() {
    renderHeader();
    renderMap();
    renderRisk();
    renderDead();
    renderSmells();
    renderBusiness();
    renderFiles();
    renderQuality();

    var counts = {
      risk: Object.keys(DATA.risk).length,
      dead: (DATA.dead_code.totals || {}).unreachable || 0,
      smells: (DATA.smells || []).length,
      business: (DATA.business || {}).total || 0,
      files: (DATA.files || []).length,
    };
    document.querySelectorAll(".tab").forEach(function (t) {
      var c = counts[t.dataset.tab];
      if (c === undefined) return;
      var badge = t.querySelector(".count");
      if (badge) badge.textContent = c;
    });
    selectTab(state.tab);
  }

  function boot() {
    document.querySelectorAll(".tab").forEach(function (t) {
      t.addEventListener("click", function () { selectTab(t.dataset.tab); });
    });
    var toggle = document.getElementById("theme-toggle");
    if (toggle) {
      toggle.addEventListener("click", function () {
        var root = document.documentElement;
        var now = root.getAttribute("data-theme");
        var next = now === "dark" ? "light" : "dark";
        root.setAttribute("data-theme", next);
        try { localStorage.setItem("cc-theme", next); } catch (e) { /* ignore */ }
      });
      try {
        var saved = localStorage.getItem("cc-theme");
        if (saved) document.documentElement.setAttribute("data-theme", saved);
      } catch (e) { /* ignore */ }
    }

    if (window.__ANALYSIS__) {
      DATA = window.__ANALYSIS__;
      renderAll();
      return;
    }
    var run = window.__RUN__ ? "?run=" + encodeURIComponent(window.__RUN__) : "";
    fetch("api/analysis" + run)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) {
          document.getElementById("panels").innerHTML =
            '<div class="empty">' + esc(d.error) + "</div>";
          return;
        }
        DATA = d;
        renderAll();
      })
      .catch(function (e) {
        document.getElementById("panels").innerHTML =
          '<div class="error">Could not load analysis: ' + esc(e.message) + "</div>";
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
