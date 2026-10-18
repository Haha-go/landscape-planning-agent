(() => {
  const API = "";
  const messagesEl = document.getElementById("messages");
  const chatInput = document.getElementById("chatInput");
  const chatSend = document.getElementById("chatSend");
  const chatMeta = document.getElementById("chatMeta");
  const chatFile = document.getElementById("chatFile");
  const fileAttachLabel = document.getElementById("fileAttachLabel");
  const fileAttachText = document.getElementById("fileAttachText");
  const modeHint = document.getElementById("modeHint");
  const welcomeEl = document.getElementById("welcome");
  const fileChip = document.getElementById("fileChip");
  const fileChipName = document.getElementById("fileChipName");
  const fileChipClear = document.getElementById("fileChipClear");
  const newChatBtn = document.getElementById("newChatBtn");

  const THREAD_KEY = "landscape_thread_id";
  const MODE_KEY = "landscape_agent_mode";
  let threadId = localStorage.getItem(THREAD_KEY) || crypto.randomUUID();
  localStorage.setItem(THREAD_KEY, threadId);

  let agentMode = localStorage.getItem(MODE_KEY) || "auto";
  if (!["auto", "kb", "plan"].includes(agentMode)) agentMode = "auto";

  const MODE_HINTS = {
    auto: "按问题自动选择规范智库或规划通；上传 PDF 需先登录。",
    kb: "锁定规范智库：规范问答与引用。请勿附带方案 PDF。",
    plan: "锁定规划通：登录后上传 PDF；可勾选是否需要人工评审。",
  };

  const MODE_LABELS = { auto: "自动", kb: "规范智库", plan: "规划通" };

  function hideWelcome() {
    if (welcomeEl) welcomeEl.hidden = true;
  }

  function showWelcome() {
    if (welcomeEl) welcomeEl.hidden = false;
  }

  function syncFileChip() {
    const f = chatFile.files && chatFile.files[0];
    if (f && agentMode !== "kb") {
      fileChip.hidden = false;
      fileChipName.textContent = f.name;
      fileAttachText.textContent = "更换";
    } else {
      fileChip.hidden = true;
      fileChipName.textContent = "";
      fileAttachText.textContent = agentMode === "kb" ? "不可用" : "附件";
    }
  }

  function autosizeInput() {
    chatInput.style.height = "auto";
    chatInput.style.height = `${Math.min(chatInput.scrollHeight, 144)}px`;
  }

  const DIM_LABELS = {
    planning_concept: "规划理念",
    compliance: "规范符合性",
    ecology: "生态可持续",
    layout: "功能布局",
    economy: "经济可行性与运营管理",
  };

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /** 教学脱敏标号 → 现行正式标准号（展示用） */
  const STANDARD_CODE_DISPLAY = {
    "CJJ/T 85-教学": "CJJ/T 85-2017",
    "CJJ/T85-教学": "CJJ/T 85-2017",
    "CJJ/T 85": "CJJ/T 85-2017",
    "GB 50180-教学": "GB 50180-2018",
    "GB50180-教学": "GB 50180-2018",
    "GB 50180": "GB 50180-2018",
    "CJJ 48-教学": "GB 51192-2016",
    "CJJ48-教学": "GB 51192-2016",
    "CJJ 48": "GB 51192-2016",
    "GB/T 51327-教学": "GB/T 51345-2018",
    "GB/T51327-教学": "GB/T 51345-2018",
    "GB/T 51327": "GB/T 51345-2018",
  };

  /** 展示用：隐藏脱敏教学用语，规范标号换成现行正式标号 */
  function sanitizeCiteText(s) {
    let text = String(s || "")
      .replace(/（脱敏教学稿）/g, "")
      .replace(/\(脱敏教学稿\)/g, "")
      .replace(/脱敏教学稿/g, "")
      .replace(/教学口径/g, "")
      .replace(/\s{2,}/g, " ")
      .trim();
    const mapped = STANDARD_CODE_DISPLAY[text] || STANDARD_CODE_DISPLAY[text.replace(/\s+/g, " ")];
    if (mapped) return mapped;
    text = text.replace(/-教学/g, "");
    return text.trim();
  }

  function sanitizeAnswerText(s) {
    let text = String(s || "")
      .replace(/（脱敏教学稿）/g, "")
      .replace(/\(脱敏教学稿\)/g, "")
      .replace(/脱敏教学稿/g, "")
      .replace(/教学口径/g, "");
    // 去掉合成示例 / 现行有效 等免责句（含模型改写残句）
    text = text
      .replace(/(?:需注意|注意)[：:]\s*[^。\n]*?(?:合成示例|现行有效|仅供参考)[^。\n]*。?/g, "")
      .replace(/[^。\n]*合成示例[^。\n]*。?/g, "")
      .replace(/[^。\n]*应以现行有效[^。\n]*。?/g, "")
      .replace(/[^。\n]*脱敏教学语料[^。\n]*。?/g, "")
      .replace(/[^。\n]*仅供参考[^。\n]*。?/g, "");
    const keys = Object.keys(STANDARD_CODE_DISPLAY).sort((a, b) => b.length - a.length);
    for (const key of keys) {
      const official = STANDARD_CODE_DISPLAY[key];
      if (key.includes("教学")) {
        text = text.split(key).join(official);
      } else {
        // 已是正式号（后跟 -20xx）时不再拼接，避免 2017-2017
        text = text.replace(
          new RegExp(key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(?!-\\d{4})", "g"),
          official
        );
      }
    }
    text = text
      .replace(/-(20\d{2})-\1\b/g, "-$1")
      .replace(/-教学/g, "")
      .replace(/《》/g, "");
    return text
      .split("\n")
      .map((line) => line.replace(/[ \t]{2,}/g, " ").trim())
      .filter(Boolean)
      .join("\n")
      .trim();
  }

  /** 标出文中规范引用：[n]、《规范名》条款、（…条款…） */
  function renderAnswerHtml(raw) {
    const escaped = escapeHtml(sanitizeAnswerText(raw));
    return escaped.replace(
      /\[\d+\]|《[^》]+》(?:\s*[\d.]+(?:\.\d+)*)?|（[^）]{1,48}）/g,
      (m) => {
        if (
          /^\[\d+\]$/.test(m) ||
          /《/.test(m) ||
          /\d+\.\d+/.test(m) ||
          /GB|CJJ|JGJ/i.test(m)
        ) {
          return `<span class="cite-ref">${m}</span>`;
        }
        return m;
      }
    );
  }

  function polishAnswerBody(bot, answerText) {
    const body = ensureBody(bot);
    const raw = answerText != null && answerText !== "" ? answerText : body.textContent || "";
    if (!raw) return;
    body.innerHTML = renderAnswerHtml(raw);
  }

  function formatCitation(c) {
    const title = sanitizeCiteText(c.title);
    const code = sanitizeCiteText(c.standard_code);
    let section = sanitizeCiteText(c.section);
    if (
      section &&
      (section === title ||
        section === code ||
        /^p?\d+$/i.test(section) ||
        section.startsWith("p"))
    ) {
      section = "";
    }
    return [title, code, section ? `§${section}` : ""]
      .filter(Boolean)
      .join(" ");
  }

  /** 正文中出现的参考资料序号，如 [1]、[3] */
  function collectCitedIndices(answerText) {
    const idxs = new Set();
    const body = String(answerText || "");
    for (const m of body.matchAll(/\[(\d+)\]/g)) {
      const n = Number(m[1]);
      if (n > 0) idxs.add(n);
    }
    return idxs;
  }

  /** 书名或正式标号是否出现在正文 */
  function citationNamedInAnswer(c, answerText) {
    const body = sanitizeAnswerText(answerText || "");
    if (!body) return false;
    const title = sanitizeCiteText(c.title);
    const code = sanitizeCiteText(c.standard_code);
    if (title && body.includes(title)) return true;
    if (code && body.includes(code)) return true;
    if (code) {
      const compactBody = body.replace(/\s+/g, "");
      const compactCode = code.replace(/\s+/g, "");
      if (compactCode && compactBody.includes(compactCode)) return true;
    }
    return false;
  }

  /**
   * 仅保留正文实际引用的规范：
   * - 点名规范名/标号，或
   * - 使用 [n] 对应检索结果第 n 条
   */
  function filterCitedStandards(list, answerText) {
    const citedIndices = collectCitedIndices(answerText);
    const byLabel = new Map();
    list.forEach((c, i) => {
      const byIndex = citedIndices.has(i + 1);
      const byName = citationNamedInAnswer(c, answerText);
      if (!byIndex && !byName) return;
      const base = formatCitation(c);
      if (!base) return;
      let entry = byLabel.get(base);
      if (!entry) {
        entry = { base, indices: new Set() };
        byLabel.set(base, entry);
      }
      if (byIndex) entry.indices.add(i + 1);
    });
    return [...byLabel.values()].map((entry) => {
      if (entry.indices.size) {
        const nums = [...entry.indices]
          .sort((a, b) => a - b)
          .map((n) => `[${n}]`)
          .join("");
        return `${nums} ${entry.base}`;
      }
      return entry.base;
    });
  }

  function listText(items) {
    if (!items || !items.length) return "—";
    return items.map((item) => escapeHtml(item)).join("；");
  }

  function listBullets(items, empty = "暂无") {
    if (!items || !items.length) {
      return `<p class="dim-empty">${empty}</p>`;
    }
    return `<ul class="dim-list">${items
      .map((item) => `<li>${escapeHtml(item)}</li>`)
      .join("")}</ul>`;
  }

  function scoreTone(score) {
    const n = Number(score);
    if (Number.isNaN(n)) return "neutral";
    if (n >= 85) return "good";
    if (n >= 70) return "mid";
    return "low";
  }

  function renderDimOverview(key, item) {
    const score = item && item.score != null ? item.score : "-";
    const tone = scoreTone(score);
    const failed = item && item.failed ? " is-failed" : "";
    return `
      <div class="score-chip tone-${tone}${failed}">
        <span class="score-chip-label">${DIM_LABELS[key] || key}</span>
        <strong class="score-chip-value">${score}</strong>
      </div>`;
  }

  function hasDimScore(item) {
    return Boolean(item && typeof item === "object" && item.score != null);
  }

  function renderDim(key, item) {
    const score = item && item.score != null ? item.score : "-";
    const tone = scoreTone(score);
    const failed =
      item && item.failed
        ? `<p class="dim-failed">失败：${escapeHtml(item.error || "")}</p>`
        : "";
    return `
      <details class="dim-block">
        <summary class="dim-item tone-${tone}">
          <span>${DIM_LABELS[key] || key}</span>
          <strong>${score}</strong>
        </summary>
        ${failed}
        <div class="dim-section"><h4>依据</h4>${listBullets(item && item.evidence)}</div>
        <div class="dim-section"><h4>风险</h4>${listBullets(item && item.risks)}</div>
        <div class="dim-section"><h4>建议</h4>${listBullets(item && item.suggestions)}</div>
      </details>`;
  }

  function renderHumanDims(humanScores) {
    const keys = Object.keys(DIM_LABELS).filter((k) =>
      hasDimScore((humanScores || {})[k])
    );
    if (!keys.length) {
      return `<p class="meta human-empty">尚未录入人工分，请在下方改分区填写后保存</p>`;
    }
    return keys.map((k) => renderDim(k, humanScores[k])).join("");
  }

  function showEvalLoading(bot) {
    bot.innerHTML = "";
    setAgentTag(bot, "规划通");
    const box = document.createElement("div");
    box.className = "eval-loading";
    box.innerHTML = `
      <div class="eval-spinner" aria-hidden="true"></div>
      <div class="eval-loading-copy">
        <p class="eval-loading-title">规划通正在评估方案</p>
        <p class="eval-loading-sub">五维并行打分中，通常需要 1～2 分钟，请稍候…</p>
        <div class="eval-loading-steps" aria-hidden="true">
          <span class="pulse-dot"></span>
          <span>解析 PDF</span>
          <span class="pulse-dot"></span>
          <span>抽取要点</span>
          <span class="pulse-dot"></span>
          <span>五维评分</span>
        </div>
      </div>`;
    bot.appendChild(box);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  const humanReviewWrap = document.getElementById("humanReviewWrap");
  const needsHumanReview = document.getElementById("needsHumanReview");

  function updateModeUI() {
    document.querySelectorAll(".mode-btn").forEach((btn) => {
      const on = btn.getAttribute("data-mode") === agentMode;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-checked", on ? "true" : "false");
    });
    modeHint.textContent = MODE_HINTS[agentMode] || MODE_HINTS.auto;
    const attachOk = agentMode !== "kb";
    if (fileAttachLabel) {
      fileAttachLabel.hidden = false;
      fileAttachLabel.classList.toggle("disabled", !attachOk);
    }
    chatFile.disabled = !attachOk;
    if (!attachOk) chatFile.value = "";
    if (humanReviewWrap) {
      humanReviewWrap.hidden = agentMode === "kb";
    }
    syncFileChip();
    chatMeta.textContent = MODE_LABELS[agentMode] || agentMode;
  }

  document.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      agentMode = btn.getAttribute("data-mode") || "auto";
      localStorage.setItem(MODE_KEY, agentMode);
      updateModeUI();
    });
  });

  document.querySelectorAll(".suggest").forEach((btn) => {
    btn.addEventListener("click", () => {
      const mode = btn.getAttribute("data-mode");
      if (mode && ["auto", "kb", "plan"].includes(mode)) {
        agentMode = mode;
        localStorage.setItem(MODE_KEY, agentMode);
        updateModeUI();
      }
      chatInput.value = btn.getAttribute("data-text") || "";
      autosizeInput();
      chatInput.focus();
    });
  });

  chatFile.addEventListener("change", () => syncFileChip());
  if (fileChipClear) {
    fileChipClear.addEventListener("click", () => {
      chatFile.value = "";
      syncFileChip();
    });
  }

  if (newChatBtn) {
    newChatBtn.addEventListener("click", () => {
      threadId = crypto.randomUUID();
      localStorage.setItem(THREAD_KEY, threadId);
      messagesEl.querySelectorAll(".bubble").forEach((el) => el.remove());
      showWelcome();
      chatInput.value = "";
      chatFile.value = "";
      autosizeInput();
      updateModeUI();
      chatInput.focus();
    });
  }

  chatInput.addEventListener("input", autosizeInput);

  function appendBubble(role, text) {
    hideWelcome();
    const div = document.createElement("div");
    div.className = `bubble ${role}`;
    if (text) div.textContent = text;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return div;
  }

  function setAgentTag(bot, agentLabel, route) {
    let tag = bot.querySelector(".agent-tag");
    if (!tag) {
      tag = document.createElement("span");
      tag.className = "agent-tag";
      bot.insertBefore(tag, bot.firstChild);
    }
    tag.textContent = agentLabel || "助手";
    if (route) {
      chatMeta.textContent = agentLabel || "助手";
    }
  }

  function ensureBody(bot) {
    let body = bot.querySelector(".body");
    if (!body) {
      body = document.createElement("span");
      body.className = "body";
      bot.appendChild(body);
    }
    return body;
  }

  function reviewStatusLabel(status) {
    const map = {
      in_review: "人工评审中",
      system_only: "仅系统评估",
      approved: "已通过",
      rejected: "已驳回",
      revision_required: "退回修改",
      failed: "失败",
      processing: "处理中",
    };
    return map[status] || status || "-";
  }

  function renderEvalCard(bot, data) {
    bot.innerHTML = "";
    setAgentTag(bot, data.agent_label || "规划通", data.intent || "eval");
    const card = document.createElement("div");
    card.className = "eval-card";
    const scores = data.scores || {};
    const overall = data.overall_score ?? "-";
    const overview = Object.keys(DIM_LABELS)
      .map((k) => renderDimOverview(k, scores[k] || {}))
      .join("");
    const details = Object.keys(DIM_LABELS)
      .map((k) => renderDim(k, scores[k] || {}))
      .join("");
    const issues = (data.issues || []).slice(0, 8);
    const issueBlock = issues.length
      ? `<section class="eval-section issues">
          <h3 class="eval-section-title">主要问题</h3>
          <ol class="issue-list">${issues
            .map((i) => `<li>${escapeHtml(i)}</li>`)
            .join("")}</ol>
        </section>`
      : "";
    const summary = (data.summary || "").trim();
    card.innerHTML = `
      <header class="eval-header">
        <div class="eval-score-wrap tone-${scoreTone(overall)}">
          <span class="eval-score-label">综合得分</span>
          <p class="score-hero">${overall}</p>
        </div>
        <div class="eval-status-wrap">
          <span class="eval-status-badge">${escapeHtml(reviewStatusLabel(data.review_status))}</span>
          <p class="eval-status-hint">点击下方维度可展开依据、风险与建议</p>
        </div>
      </header>
      ${
        summary
          ? `<section class="eval-section">
              <h3 class="eval-section-title">评估摘要</h3>
              <p class="summary">${escapeHtml(summary)}</p>
            </section>`
          : ""
      }
      <section class="eval-section">
        <h3 class="eval-section-title">五维得分</h3>
        <div class="score-chip-grid">${overview}</div>
      </section>
      ${issueBlock}
      <section class="eval-section">
        <h3 class="eval-section-title">维度明细</h3>
        <div class="dim-grid">${details}</div>
      </section>
    `;
    bot.appendChild(card);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  async function consumeSSE(res, bot) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let eventName = "message";
    let agentLabel = "";
    let routeTag = "";
    const citations = [];

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n");
      buffer = parts.pop() || "";
      for (const line of parts) {
        if (line.startsWith("event:")) {
          eventName = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          const raw = line.slice(5).trim();
          if (!raw) continue;
          let data;
          try {
            data = JSON.parse(raw);
          } catch {
            continue;
          }
          if (eventName === "orchestrator") {
            agentLabel = data.agent_label || agentLabel;
            setAgentTag(bot, agentLabel || "助手", data.intent);
          } else if (eventName === "route" && data.route) {
            routeTag = data.route;
            setAgentTag(bot, agentLabel || "规范智库", routeTag);
          } else if (eventName === "token" && data.token) {
            if (!bot.querySelector(".agent-tag")) {
              setAgentTag(bot, agentLabel || "助手", routeTag);
            }
            ensureBody(bot).textContent += data.token;
          } else if (eventName === "citation") {
            citations.push(data);
          } else if (eventName === "done") {
            if (data.agent_label) agentLabel = data.agent_label;
            if (data.thread_id) {
              threadId = data.thread_id;
              localStorage.setItem(THREAD_KEY, threadId);
            }
            chatMeta.textContent = agentLabel || "助手";
            setAgentTag(bot, agentLabel || "助手", data.route || routeTag);
            polishAnswerBody(bot, data.answer);
            const answerText =
              (data.answer != null && data.answer !== ""
                ? data.answer
                : ensureBody(bot).textContent) || "";
            const list = citations.length ? citations : data.citations || [];
            if (list.length) {
              const unique = filterCitedStandards(list, answerText);
              if (unique.length) {
                const cite = document.createElement("div");
                cite.className = "citations";
                cite.textContent = "参考规范：" + unique.join("； ");
                bot.appendChild(cite);
              }
            }
          } else if (eventName === "error") {
            bot.textContent = data.message || "出错了";
          }
          messagesEl.scrollTop = messagesEl.scrollHeight;
        } else if (line === "") {
          eventName = "message";
        }
      }
    }
  }

  async function sendChat() {
    const message = chatInput.value.trim();
    const file = chatFile.files && chatFile.files[0];
    if (!message && !file) return;
    if (agentMode === "plan" && !file && !message) return;

    if (file && agentMode !== "kb") {
      const tok = localStorage.getItem("lpa_token") || "";
      if (!tok) {
        appendBubble("user", `${message || "（上传方案）"}\n📎 ${file.name}`);
        appendBubble(
          "assistant",
          "上传方案需先登录。请使用提交人账号 submitter / submitter123（页面下方登录）。"
        );
        document.getElementById("sessionBar")?.scrollIntoView({ behavior: "smooth" });
        return;
      }
    }

    chatInput.value = "";
    chatSend.disabled = true;

    const wantHuman = !needsHumanReview || needsHumanReview.checked;
    const userText = file
      ? `${message || "（上传方案）"}\n📎 ${file.name}${wantHuman ? "" : "\n（仅系统评估）"}`
      : message;
    appendBubble("user", userText);
    const bot = appendBubble("assistant", "");
    const willEval = Boolean(file && agentMode !== "kb");
    if (willEval) {
      showEvalLoading(bot);
    }

    try {
      const fd = new FormData();
      fd.append("message", message || (file ? "请评估这份规划方案" : ""));
      fd.append("thread_id", threadId);
      fd.append("agent_mode", agentMode);
      fd.append("needs_human_review", wantHuman ? "true" : "false");
      if (file && agentMode !== "kb") {
        fd.append("file", file);
      }

      const headers = {};
      const tok = localStorage.getItem("lpa_token") || "";
      if (tok) headers.Authorization = `Bearer ${tok}`;

      const res = await fetch(`${API}/api/v1/assistant`, {
        method: "POST",
        headers,
        body: fd,
      });

      const ct = (res.headers.get("content-type") || "").toLowerCase();
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const err = await res.json();
          detail = err.detail || detail;
        } catch {
          /* ignore */
        }
        if (res.status === 401) {
          detail = "请先登录后再上传方案（submitter / submitter123）";
        }
        throw new Error(detail);
      }

      if (ct.includes("application/json")) {
        const data = await res.json();
        if (data.intent === "eval" || data.scores) {
          renderEvalCard(bot, data);
          const humanNote =
            data.review_status === "system_only"
              ? "仅系统评估"
              : data.review_status === "in_review"
                ? "已进入人工评审队列"
                : data.review_status || "";
          chatMeta.textContent = `${data.agent_label || "规划通"} · ${humanNote}`;
          if (typeof loadMyPlans === "function") loadMyPlans();
        } else {
          bot.textContent = data.message || data.detail || JSON.stringify(data);
        }
      } else {
        await consumeSSE(res, bot);
      }

      chatFile.value = "";
      syncFileChip();
      autosizeInput();
    } catch (err) {
      bot.textContent = `请求失败：${err.message}`;
    } finally {
      chatSend.disabled = false;
      chatInput.focus();
      updateModeUI();
    }
  }

  chatSend.addEventListener("click", sendChat);
  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChat();
    }
  });

  updateModeUI();

  // —— 登录 / 角色视图 ——
  const TOKEN_KEY = "lpa_token";
  const ROLE_KEY = "lpa_role";
  const loginForm = document.getElementById("loginForm");
  const loginMeta = document.getElementById("loginMeta");
  const reviewTools = document.getElementById("reviewTools");
  const reviewQueue = document.getElementById("reviewQueue");
  const reviewDetail = document.getElementById("reviewDetail");
  let token = localStorage.getItem(TOKEN_KEY) || "";

  function authHeaders() {
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  function currentRole() {
    return localStorage.getItem(ROLE_KEY) || "";
  }

  function isExpert() {
    return ["reviewer", "admin"].includes(currentRole());
  }

  const reviewReport = document.getElementById("reviewReport");
  const reviewGate = document.getElementById("reviewGate");
  const logoutBtn = document.getElementById("logoutBtn");
  const workbenchPanel = document.getElementById("workbench");
  const myPlansPanel = document.getElementById("myPlans");
  const myPlansList = document.getElementById("myPlansList");
  const myPlanDetail = document.getElementById("myPlanDetail");
  const navMyPlans = document.getElementById("navMyPlans");
  const navWorkbench = document.getElementById("navWorkbench");

  function pct(v) {
    if (v == null || Number.isNaN(v)) return "—";
    return `${(Number(v) * 100).toFixed(1)}%`;
  }

  function num(v, digits = 1) {
    if (v == null || Number.isNaN(v)) return "—";
    return Number(v).toFixed(digits);
  }

  function card(label, value) {
    return `<div class="report-card"><span class="label">${escapeHtml(label)}</span><span class="value">${escapeHtml(value)}</span></div>`;
  }

  function showSession() {
    const role = currentRole();
    const loggedIn = Boolean(token);
    if (!loggedIn) {
      loginMeta.textContent = "未登录 · 问答可匿名，上传方案请登录提交人";
      if (reviewTools) reviewTools.hidden = true;
      if (reviewGate) reviewGate.hidden = false;
      if (logoutBtn) logoutBtn.hidden = true;
      if (workbenchPanel) workbenchPanel.hidden = true;
      if (myPlansPanel) myPlansPanel.hidden = true;
      if (navMyPlans) navMyPlans.hidden = true;
      if (navWorkbench) navWorkbench.hidden = true;
      return;
    }
    const roleLabel =
      role === "submitter"
        ? "提交人"
        : role === "reviewer"
          ? "评审专家"
          : role === "admin"
            ? "管理员"
            : role;
    loginMeta.textContent = `已登录 · ${roleLabel}`;
    if (logoutBtn) logoutBtn.hidden = false;

    const expert = isExpert();
    if (workbenchPanel) workbenchPanel.hidden = !expert;
    if (navWorkbench) navWorkbench.hidden = !expert;
    if (reviewTools) reviewTools.hidden = !expert;
    if (reviewGate) reviewGate.hidden = expert ? true : false;

    // 评审专家 / 管理员只做审核，不展示「我的方案」
    if (myPlansPanel) {
      myPlansPanel.hidden = expert;
      if (expert) myPlansPanel.open = false;
    }
    if (navMyPlans) navMyPlans.hidden = expert;
    updateModeUI();
  }

  function clearSession() {
    token = "";
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(ROLE_KEY);
    if (reviewReport) reviewReport.innerHTML = "";
    if (reviewQueue) reviewQueue.innerHTML = "";
    if (myPlansList) myPlansList.innerHTML = "";
    if (myPlanDetail) {
      myPlanDetail.hidden = true;
      myPlanDetail.innerHTML = "";
    }
    showSession();
  }

  async function loadMyPlans() {
    if (!token || !myPlansList) return;
    const res = await fetch(`${API}/api/v1/plans/mine`, { headers: authHeaders() });
    const data = await res.json();
    if (!res.ok) {
      myPlansList.textContent = data.detail || "无法加载我的方案";
      return;
    }
    myPlansList.innerHTML = "";
    (data.items || []).forEach((item) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "queue-item";
      const name = item.file_name || item.id;
      btn.textContent = `${name} · 系统 ${item.overall_score ?? "-"} · ${reviewStatusLabel(item.review_status)}`;
      btn.addEventListener("click", () => openMyPlan(item.id));
      myPlansList.appendChild(btn);
    });
    if (!data.items || !data.items.length) {
      myPlansList.textContent = "暂无上传记录";
    }
  }

  async function openMyPlan(id) {
    if (!myPlanDetail) return;
    const res = await fetch(`${API}/api/v1/plans/${id}`, { headers: authHeaders() });
    const data = await res.json();
    if (!res.ok) {
      myPlanDetail.hidden = false;
      myPlanDetail.textContent = data.detail || "无法打开";
      return;
    }
    const systemDims = Object.keys(DIM_LABELS)
      .map((k) => renderDim(k, (data.scores || {})[k] || {}))
      .join("");
    myPlanDetail.hidden = false;
    myPlanDetail.innerHTML = `
      <p class="score-hero">${escapeHtml(data.file_name || id)}</p>
      <p class="meta">${escapeHtml(reviewStatusLabel(data.review_status))} · 终审 ${escapeHtml(data.final_decision || "未决")} · 总分 ${data.overall_score ?? "-"}</p>
      <p class="summary">${escapeHtml(data.summary || "")}</p>
      <div>${systemDims}</div>
    `;
  }

  loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = document.getElementById("loginUser").value.trim();
    const password = document.getElementById("loginPass").value;
    const res = await fetch(`${API}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (!res.ok) {
      loginMeta.textContent = data.detail || "登录失败";
      return;
    }
    token = data.access_token;
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(ROLE_KEY, data.role || "");
    showSession();
    if (isExpert()) {
      if (workbenchPanel) {
        workbenchPanel.hidden = false;
        workbenchPanel.open = true;
      }
      if (myPlansPanel) {
        myPlansPanel.hidden = true;
        myPlansPanel.open = false;
      }
      loadQueue();
      loadReport();
    } else {
      loadMyPlans();
      if (myPlansPanel) {
        myPlansPanel.hidden = false;
        myPlansPanel.open = true;
      }
    }
  });

  if (logoutBtn) {
    logoutBtn.addEventListener("click", () => clearSession());
  }

  if (workbenchPanel) {
    workbenchPanel.addEventListener("toggle", () => {
      if (workbenchPanel.open && token && isExpert()) {
        loadQueue();
        loadReport();
      }
    });
  }

  if (myPlansPanel) {
    myPlansPanel.addEventListener("toggle", () => {
      if (myPlansPanel.open && token) loadMyPlans();
    });
  }

  const reloadMyPlansBtn = document.getElementById("reloadMyPlans");
  if (reloadMyPlansBtn) {
    reloadMyPlansBtn.addEventListener("click", loadMyPlans);
  }

  function openHashTarget() {
    const hash = (location.hash || "").replace("#", "");
    if (hash === "workbench" && workbenchPanel && isExpert()) {
      workbenchPanel.hidden = false;
      workbenchPanel.open = true;
      if (token) {
        loadQueue();
        loadReport();
      }
    }
    if (hash === "myPlans" && myPlansPanel && token && !isExpert()) {
      myPlansPanel.hidden = false;
      myPlansPanel.open = true;
      loadMyPlans();
    }
  }
  window.addEventListener("hashchange", openHashTarget);

  async function loadReport() {
    if (!token || !reviewReport || !isExpert()) return;
    const res = await fetch(`${API}/api/v1/reviews/report`, { headers: authHeaders() });
    const data = await res.json();
    if (!res.ok) {
      reviewReport.textContent = data.detail || "无法加载报表";
      return;
    }
    const backlog = data.backlog || {};
    const ages = backlog.age_buckets || {};
    reviewReport.innerHTML = [
      card("通过率", pct(data.pass_rate)),
      card("已终审", String(data.decided_count ?? 0)),
      card("平均改分差", num(data.avg_abs_score_diff, 2)),
      card("积压(审核中)", String(backlog.in_review ?? 0)),
      card("未认领", String(backlog.unassigned ?? 0)),
      `<div class="report-table">积压账龄：&lt;1天 ${ages.lt_1d ?? 0} · 1–3天 ${ages.d1_to_3d ?? 0} · &gt;3天 ${ages.gt_3d ?? 0}</div>`,
    ].join("");
  }

  async function loadQueue() {
    if (!token || !isExpert()) return;
    const status = document.getElementById("statusFilter").value;
    const qs = new URLSearchParams({ page: "1", page_size: "20" });
    if (status) qs.set("review_status", status);
    const res = await fetch(`${API}/api/v1/reviews?${qs}`, { headers: authHeaders() });
    const data = await res.json();
    if (!res.ok) {
      reviewQueue.textContent = data.detail || "无法加载队列";
      return;
    }
    reviewQueue.innerHTML = "";
    (data.items || []).forEach((item) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "queue-item";
      const name = item.file_name || item.id;
      btn.textContent = `${name} · 系统 ${item.overall_score ?? "-"} / 人工 ${item.human_overall ?? "-"} · ${item.review_status || ""}`;
      btn.addEventListener("click", () => openReview(item.id));
      reviewQueue.appendChild(btn);
    });
    if (!data.items || !data.items.length) {
      reviewQueue.textContent = "队列为空";
    }
  }

  document.getElementById("reloadQueue").addEventListener("click", loadQueue);
  document.getElementById("reloadReport").addEventListener("click", loadReport);
  document.getElementById("statusFilter").addEventListener("change", loadQueue);

  async function openReview(id) {
    if (!isExpert()) {
      return openMyPlan(id);
    }
    const res = await fetch(`${API}/api/v1/plans/${id}`, { headers: authHeaders() });
    const data = await res.json();
    if (!res.ok) {
      reviewDetail.hidden = false;
      reviewDetail.textContent = data.detail || "无法打开";
      return;
    }
    const systemDims = Object.keys(DIM_LABELS)
      .map((k) => renderDim(k, (data.scores || {})[k] || {}))
      .join("");
    const humanDims = renderHumanDims(data.human_scores || {});
    const summaryHtml = (data.summary || "").trim()
      ? `<div class="eval-section review-summary">
          <h3 class="eval-section-title">评估摘要</h3>
          <p class="summary">${escapeHtml(data.summary)}</p>
        </div>`
      : "";
    const inputs = Object.keys(DIM_LABELS)
      .map((k) => {
        const current = ((data.human_scores || {})[k] || {}).score;
        const fallback = ((data.scores || {})[k] || {}).score;
        const value = current != null ? current : fallback != null ? fallback : "";
        return `<label class="score-edit">${DIM_LABELS[k]} <input data-dim="${k}" type="number" min="0" max="100" step="0.1" value="${value}" /></label>`;
      })
      .join("");
    const role = localStorage.getItem(ROLE_KEY) || "";
    reviewDetail.hidden = false;
    reviewDetail.innerHTML = `
      <p class="score-hero">${escapeHtml(data.file_name || id)}</p>
      <p class="meta">${escapeHtml(reviewStatusLabel(data.review_status))} · 终审 ${escapeHtml(data.final_decision || "未决")} · 认领 ${escapeHtml(data.assignee_id || "无")}</p>
      <div class="decision-row">
        <button type="button" id="claimBtn">认领</button>
        ${role === "admin" ? `<span class="assign-row"><input id="assignTo" placeholder="改派给 user_id" /><button type="button" id="assignBtn" class="ghost">改派</button></span>` : ""}
      </div>
      <div class="compare">
        <div class="compare-col">
          <h3>系统分 ${data.overall_score ?? "-"}</h3>
          ${systemDims}
          ${summaryHtml}
        </div>
        <div class="compare-col">
          <h3>人工分 ${data.human_overall ?? "-"}</h3>
          ${humanDims}
        </div>
      </div>
      <h3 class="eval-section-title">改分录入</h3>
      <div class="score-edit">${inputs}</div>
      <textarea id="reviewComment" rows="3" placeholder="审核意见"></textarea>
      <div class="decision-row">
        <button type="button" id="saveScores">保存人工分</button>
        <button type="button" data-decision="approved">通过</button>
        <button type="button" data-decision="rejected" class="ghost">驳回</button>
        <button type="button" data-decision="revision_required" class="ghost">退回修改</button>
      </div>
      <div id="actionLog" class="meta"></div>
    `;
    document.getElementById("claimBtn").addEventListener("click", () => postAction(id, "claim"));
    const assignBtn = document.getElementById("assignBtn");
    if (assignBtn) {
      assignBtn.addEventListener("click", () => {
        const reviewerId = document.getElementById("assignTo").value.trim();
        postAction(id, "assign", { reviewer_id: reviewerId });
      });
    }
    document.getElementById("saveScores").addEventListener("click", () => saveScores(id, data));
    reviewDetail.querySelectorAll("[data-decision]").forEach((btn) => {
      btn.addEventListener("click", () => {
        postAction(id, "decision", {
          decision: btn.getAttribute("data-decision"),
          comment: document.getElementById("reviewComment").value,
        });
      });
    });
    loadActions(id);
  }

  async function postAction(id, kind, body) {
    const res = await fetch(`${API}/api/v1/reviews/${id}/${kind}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(body || {}),
    });
    const data = await res.json();
    if (!res.ok) {
      loginMeta.textContent = data.detail || "操作失败";
      return;
    }
    loginMeta.textContent = "已更新";
    await openReview(id);
    loadQueue();
  }

  async function saveScores(id, current) {
    const scores = {};
    reviewDetail.querySelectorAll("input[data-dim]").forEach((input) => {
      const dim = input.getAttribute("data-dim");
      const system = (current.scores || {})[dim] || {};
      scores[dim] = {
        score: Number(input.value),
        evidence: system.evidence || [],
        risks: system.risks || [],
        suggestions: system.suggestions || [],
      };
    });
    const res = await fetch(`${API}/api/v1/reviews/${id}/scores`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({
        scores,
        recompute_overall: true,
        comment: document.getElementById("reviewComment").value,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      loginMeta.textContent = data.detail || "改分失败";
      return;
    }
    await openReview(id);
  }

  async function loadActions(id) {
    const res = await fetch(`${API}/api/v1/reviews/${id}/actions`, { headers: authHeaders() });
    const data = await res.json();
    const el = document.getElementById("actionLog");
    if (!el || !res.ok) return;
    const lines = (data.items || []).map((item) => `${item.action} · ${item.actor_id}`);
    el.textContent = lines.length ? `审计：${lines.join(" ｜ ")}` : "尚无审计";
  }

  showSession();
  openHashTarget();
  if (token) {
    if (isExpert()) {
      loadQueue();
      loadReport();
    } else {
      loadMyPlans();
    }
  }
})();
