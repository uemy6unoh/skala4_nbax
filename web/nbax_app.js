// nbax_app.js — 냉장고를 부탁해 AX 프런트 로직
// 두 가지 모드를 자동 감지:
//   · 라이브 모드  (nbax_server.py, http://localhost) : 클릭 시 에이전트가 실제 실행됨
//   · 정적 모드   (nbax_run.py 결과를 file:// 로 열람) : nbax_data.js에 있는 결과만 표시
// 페이지 흐름:
//   홈(타입 선택) --레시피 생성--> 레시피 --이 레시피로 장보기--> 장보기(A+B)
//   홈 --바로 장보기--> 장보기(A만)

(function () {
  "use strict";

  const CUISINES = ["한식", "중식", "양식", "일식"];
  const PIPELINE_VERSION = "agent-context-v7";
  const $ = (id) => document.getElementById(id);
  const LIVE = location.protocol === "http:" || location.protocol === "https:";
  const HAS_CURRENT_DATA =
    !!window.NBAX_DATA && window.NBAX_DATA.pipeline_version === PIPELINE_VERSION;
  const DATA = HAS_CURRENT_DATA
    ? window.NBAX_DATA
    : { recipes: {}, shopping: {} };

  if (!LIVE && !HAS_CURRENT_DATA) {
    document.querySelector(".wrap").innerHTML =
      '<div class="card" style="margin-top:60px"><h1>데이터가 없습니다</h1>' +
      '<p class="desc">라이브 모드로 실행하거나, 배치로 미리 생성하세요.</p>' +
      '<div class="md"><p><code>python src/nbax_server.py</code> (웹에서 실시간 생성)</p>' +
      '<p><code>python src/nbax_run.py</code> (미리 생성)</p></div></div>';
    return;
  }

  // ---------- 초소형 마크다운 렌더러 ----------
  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function inline(s) {
    return s
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
               '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      // '(임박)' 표시 재료 강조: 이거 써야 해서 쓴다는 느낌의 빨강~주황
      .replace(/\(임박\)/g, '<span class="imbak">임박 소진</span>')
      // 브리핑 임박 목록의 D-day 표기(D-0~D-5)도 같은 톤으로 강조
      .replace(/\(D-([0-5])\)/g, '<span class="imbak">D-$1</span>');
  }
  function renderMd(md) {
    const lines = md.split("\n");
    let html = "", i = 0, listType = null;
    const closeList = () => { if (listType) { html += `</${listType}>`; listType = null; } };
    while (i < lines.length) {
      const raw = lines[i];
      const line = esc(raw.trim());
      // 표 블록
      if (raw.trim().startsWith("|") && raw.includes("|", 1)) {
        closeList();
        const rows = [];
        while (i < lines.length && lines[i].trim().startsWith("|")) { rows.push(lines[i].trim()); i++; }
        html += "<table>";
        rows.forEach((r, idx) => {
          if (/^\|[\s:|-]+\|$/.test(r)) return; // |---|---| 구분선
          const cells = r.slice(1, r.endsWith("|") ? -1 : undefined).split("|");
          const tag = idx === 0 ? "th" : "td";
          html += "<tr>" + cells.map(c => `<${tag}>${inline(esc(c.trim()))}</${tag}>`).join("") + "</tr>";
        });
        html += "</table>";
        continue;
      }
      const h = line.match(/^(#{1,3})\s+(.*)/);
      const ol = line.match(/^\d+[.)]\s+(.*)/);
      const ul = line.match(/^[-*]\s+(.*)/);
      if (h) { closeList(); const n = h[1].length + 1; html += `<h${n}>${inline(h[2])}</h${n}>`; }
      else if (/^(-{3,}|\*{3,})$/.test(line)) { closeList(); html += "<hr>"; }
      else if (ol) { if (listType !== "ol") { closeList(); html += "<ol>"; listType = "ol"; } html += `<li>${inline(ol[1])}</li>`; }
      else if (ul) { if (listType !== "ul") { closeList(); html += "<ul>"; listType = "ul"; } html += `<li>${inline(ul[1])}</li>`; }
      else if (line === "") { closeList(); }
      else { closeList(); html += `<p>${inline(line)}</p>`; }
      i++;
    }
    closeList();
    return html;
  }

  function renderShopping(md) {
    const lines = md.split("\n");
    const headingIndex = lines.findIndex(line => /보충 구매 목록/.test(line));
    const summary = headingIndex >= 0 ? lines.slice(0, headingIndex).join("\n") : "";
    const items = [];
    const itemPattern =
      /^-\s*\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)\s*\(([^)]+)\)/;

    lines.forEach(line => {
      const match = line.trim().match(itemPattern);
      if (!match) return;
      const remaining = match[3]
        .replace(/^현재(?:\s*재고)?\s*[:：]\s*/, "")
        .trim();
      items.push({ name: match[1], url: match[2], remaining });
    });

    if (!items.length) return renderMd(md);

    const cards = items.map(item =>
      `<div class="shop-item">` +
        `<div class="shop-item-info"><strong>${esc(item.name)}</strong>` +
        `<span>현재 ${esc(item.remaining)}</span></div>` +
        `<a class="shop-go" href="${esc(item.url)}" target="_blank" rel="noopener">바로가기</a>` +
      `</div>`
    ).join("");

    return `${summary ? `<div class="shop-summary">${renderMd(summary)}</div>` : ""}` +
      `<h2 class="shop-list-title">🛒 보충 구매 목록</h2>` +
      `<div class="shop-grid">${cards}</div>`;
  }

  function parseCsv(text) {
    const rows = [];
    let row = [], cell = "", quoted = false;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (ch === '"') {
        if (quoted && text[i + 1] === '"') { cell += '"'; i++; }
        else quoted = !quoted;
      } else if (ch === "," && !quoted) {
        row.push(cell); cell = "";
      } else if ((ch === "\n" || ch === "\r") && !quoted) {
        if (ch === "\r" && text[i + 1] === "\n") i++;
        row.push(cell); cell = "";
        if (row.some(value => value !== "")) rows.push(row);
        row = [];
      } else {
        cell += ch;
      }
    }
    if (cell || row.length) { row.push(cell); rows.push(row); }
    return rows;
  }

  function renderFridgeInventory(csvText) {
    const box = $("fridge-grid");
    if (!csvText) {
      box.innerHTML = '<div class="fridge-empty">재고 데이터를 불러오는 중...</div>';
      return;
    }
    const rows = parseCsv(csvText);
    if (rows.length < 2) {
      box.innerHTML = '<div class="fridge-empty">표시할 재고가 없습니다.</div>';
      return;
    }
    const header = rows[0];
    const index = name => header.indexOf(name);
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    box.innerHTML = rows.slice(1).map(row => {
      const name = row[index("재료명")] || "";
      const category = row[index("카테고리")] || "";
      const amount = `${row[index("수량")] || ""}${row[index("단위")] || ""}`;
      const expiry = row[index("유통기한")] || "";
      const expiryDate = new Date(`${expiry}T00:00:00`);
      const days = Math.round((expiryDate - today) / 86400000);
      const stateClass = days < 0 ? " expired" : days <= 5 ? " imminent" : "";
      const state = days < 0 ? `D+${Math.abs(days)}` : `D-${days}`;
      return `<div class="fridge-item${stateClass}">` +
        `<div class="fridge-item-main"><strong>${esc(name)}</strong><b>${esc(amount)}</b></div>` +
        `<small>${esc(category)} · ${esc(expiry)} · ${state}</small>` +
      `</div>`;
    }).join("");
  }

  // ---------- 뷰 전환 / 로딩 ----------
  function go(view) {
    document.querySelectorAll(".view").forEach(v => v.classList.remove("on"));
    $("view-" + view).classList.add("on");
    window.scrollTo({ top: 0 });
  }
  document.querySelectorAll(".back").forEach(b =>
    b.addEventListener("click", () => go(b.dataset.go)));

  function loadingOn(msg) { $("loading-msg").textContent = msg; $("loading").classList.add("on"); }
  function loadingOff() { $("loading").classList.remove("on"); }

  async function api(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  // task(): 라이브 모드면 실제 API 호출, 정적 모드면 짧은 연출 후 로컬 데이터 반환
  function task(msg, liveFn, staticFn) {
    loadingOn(msg);
    if (LIVE) {
      return liveFn().finally(loadingOff);
    }
    return new Promise((resolve, reject) => {
      setTimeout(() => {
        loadingOff();
        try { resolve(staticFn()); } catch (e) { reject(e); }
      }, 800);
    });
  }

  // ---------- 홈: 요리 타입 선택 ----------
  let selected = null;
  let userPicked = false; // 사용자가 직접 고른 뒤에는 추천이 덮어쓰지 않음
  const chips = $("cuisine-chips");
  const chipEls = {};

  function pick(c) {
    if (!chipEls[c] || chipEls[c].disabled) return;
    chips.querySelectorAll("button").forEach(x => x.classList.remove("on"));
    chipEls[c].classList.add("on");
    selected = c;
  }

  CUISINES.forEach(c => {
    const b = document.createElement("button");
    b.textContent = c;
    const usable = LIVE || !!DATA.recipes[c]; // 라이브 모드는 전부 선택 가능
    if (!usable) {
      b.disabled = true;
      b.title = `nbax_run.py --cuisine ${c} 로 생성하거나 nbax_server.py로 실행하세요`;
    } else {
      b.addEventListener("click", () => { userPicked = true; pick(c); });
      if (!selected) { selected = c; b.classList.add("on"); } // 기본 선택 (추천 오면 교체)
    }
    chipEls[c] = b;
    chips.appendChild(b);
  });

  // 홈 상단에 오늘 날짜 표시 (냉장고 브리핑의 기준일)
  (function showToday() {
    const d = new Date();
    const dow = ["일", "월", "화", "수", "목", "금", "토"][d.getDay()];
    $("today").textContent =
      `${d.getFullYear()}년 ${d.getMonth() + 1}월 ${d.getDate()}일 (${dow}) 기준`;
  })();
  if (DATA.csv) {
    renderFridgeInventory(DATA.csv);
  } else if (LIVE) {
    renderFridgeInventory("");
    fetch("/nbax_fridge.csv?v=7", { cache: "no-store" })
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.text();
      })
      .then(csv => {
        DATA.csv = csv;
        renderFridgeInventory(csv);
      })
      .catch(e => {
        $("fridge-grid").innerHTML =
          `<div class="fridge-empty">재고 로드 실패: ${esc(e.message)}</div>`;
      });
  } else {
    renderFridgeInventory("");
  }

  const NO_RECIPE = "적합한 레시피 없음"; // 요리사의 정직한 실패 문구 (nbax_agents.py와 동일)

  function showRecipe(cuisine, md) {
    const none = md.trim().startsWith(NO_RECIPE);
    $("recipe-title").innerHTML =
      `${cuisine} 요리사의 ${none ? "답변" : "레시피"} <span class="badge">Agent 2 생성 · Agent 3 검증</span>`;
    $("recipe-body").innerHTML = none
      ? `<div class="empty">현재 재고로는 ${cuisine} 요리를 억지로 만들지 않았어요. ` +
        `홈으로 돌아가 다른 타입을 선택해 보세요.</div>` + renderMd(md)
      : renderMd(md);
    $("btn-shop-recipe").hidden = none; // 요리를 안 했으니 장보기 버튼 숨김
    go("recipe");
  }
  function showShopping(title, desc, md) {
    $("shopping-title").innerHTML = `${title} <span class="badge">Agent 4 · ReAct</span>`;
    $("shopping-desc").textContent = desc;
    $("shopping-body").innerHTML = renderShopping(md);
    go("shopping");
  }

  // ---------- 홈 → 레시피 ----------
  function genRecipe(c) {
    const recipeStatus = DATA.recipes[c]
      ? `${c} 레시피를 불러오는 중...`
      : `Agent 2 ${c} 요리사가 레시피를 작성하고 Agent 3 검증자가 확인하는 중...`;
    task(
      recipeStatus,
      async () => {
        const r = await api("/api/recipe", { cuisine: c });
        // '적합한 레시피 없음'은 캐시하지 않음 -> 다시 누르면 재시도
        if (!r.recipe.trim().startsWith(NO_RECIPE)) DATA.recipes[c] = r.recipe;
        showRecipe(c, r.recipe);
      },
      () => showRecipe(c, DATA.recipes[c]),
    ).catch(e => alert("레시피 생성 실패: " + e.message));
  }
  $("btn-recipe").addEventListener("click", () => {
    if (!selected) { alert("생성된 레시피가 없습니다. nbax_server.py로 실행해 보세요."); return; }
    genRecipe(selected);
  });

  // ---------- 레시피 → 요리 후 장보기 (소진 재료 보충) ----------
  const RECIPE_SHOP_DESC =
    "레시피대로 요리해 재료를 소진한 뒤, 냉장고를 다시 채우는 보충 구매 추천입니다.";
  $("btn-shop-recipe").addEventListener("click", () => {
    const c = selected, key = "recipe:" + c;
    if (!LIVE && !DATA.shopping[key]) {
      showShopping("🛒 요리 후 장보기 · " + c, "",
        `이 레시피의 장보기 결과가 없습니다. nbax_server.py로 실행하거나 ` +
        `**nbax_run.py --cuisine ${c}** 로 생성하세요.`);
      return;
    }
    task(
      "Agent 4 장보기 관리사가 요리 후 남은 재고를 확인하는 중...",
      async () => {
        const r = await api("/api/shopping", { mode: "recipe", cuisine: c });
        DATA.shopping[key] = r.shopping;
        showShopping(`🛒 요리 후 장보기 · ${c}`, RECIPE_SHOP_DESC, r.shopping);
      },
      () => showShopping(`🛒 요리 후 장보기 · ${c}`, RECIPE_SHOP_DESC, DATA.shopping[key]),
    ).catch(e => alert("장보기 생성 실패: " + e.message));
  });

  // ---------- 홈 → 바로 장보기 (현 재고 보충) ----------
  const DIRECT_SHOP_DESC = "현 재고 기준으로 부족하거나 곧 없어질 재료를 채우는 보충 구매 추천입니다.";
  $("btn-direct").addEventListener("click", () => {
    if (!LIVE && !DATA.shopping["direct"]) {
      alert("바로 장보기 결과가 없습니다. nbax_server.py로 실행해 보세요.");
      return;
    }
    task(
      "Agent 4 장보기 관리사가 냉장고 재고를 확인하는 중...",
      async () => {
        const r = await api("/api/shopping", { mode: "direct" });
        DATA.shopping["direct"] = r.shopping;
        showShopping("🛒 바로 장보기", DIRECT_SHOP_DESC, r.shopping);
      },
      () => showShopping("🛒 바로 장보기", DIRECT_SHOP_DESC, DATA.shopping["direct"]),
    ).catch(e => alert("장보기 생성 실패: " + e.message));
  });

  // ---------- 홈 브리핑: 접속하자마자 Agent 1 자동 실행 ----------
  // 화면 출력: 마감 임박 재료 + (폐기 경고)뿐. 추천은 섹션 없이,
  // 맨 끝 '추천타입: X' 줄만 파싱해 해당 칩에 ⭐를 달고 자동 선택한다 (줄 자체는 숨김).
  function applyBrief(md) {
    const m = md.match(/추천타입\s*[:：]\s*(한식|중식|양식|일식)/);
    const display = md.replace(/\n?추천타입\s*[:：].*$/m, "").trim(); // 파싱용 줄은 화면에서 숨김
    $("agent-brief").innerHTML = renderMd(display);
    if (m) {
      const c = m[1];
      if (chipEls[c]) chipEls[c].textContent = `⭐ ${c}`;
      if (!userPicked) pick(c); // 사용자가 아직 안 골랐으면 추천 타입 자동 선택
    }
  }

  (function initBrief() {
    const box = $("agent-brief");
    if (DATA.fridge_report) {
      applyBrief(DATA.fridge_report);
    } else if (LIVE) {
      box.innerHTML =
        '<div class="inline-loading"><div class="spinner"></div>Agent 1 냉장고 관리사가 임박 재료를 확인하는 중...</div>';
      api("/api/fridge", {}).then(r => {
        DATA.fridge_report = r.fridge_report;
        DATA.csv = r.csv;
        applyBrief(r.fridge_report);
        renderFridgeInventory(r.csv);
      }).catch(e => {
        box.innerHTML = `<div class="empty">브리핑 생성 실패: ${esc(e.message)}</div>`;
        $("fridge-grid").innerHTML =
          `<div class="fridge-empty">재고 로드 실패: ${esc(e.message)}</div>`;
      });
    } else {
      box.innerHTML = '<div class="empty">nbax_run.py를 실행하면 브리핑이 생성됩니다.</div>';
    }
  })();

  // ---------- 메타 ----------
  $("meta").textContent = LIVE
    ? `라이브 모드 (nbax_server.py) · 클릭 시 에이전트가 실시간 실행됩니다` +
      (DATA.generated_at ? ` · 캐시: ${DATA.generated_at}` : "")
    : `정적 모드 · 생성 시각 ${DATA.generated_at} · 모델 ${DATA.model} · ` +
      `생성된 요리: ${Object.keys(DATA.recipes).join(", ") || "없음"}`;
})();
