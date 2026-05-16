const state = {
  user: null,
  mode: "login",
  view: "today",
  query: "",
  tag: "",
  cards: [],
  tags: [],
  notifySeen: new Set(),
};

const els = {
  authView: document.querySelector("#authView"),
  appView: document.querySelector("#appView"),
  authForm: document.querySelector("#authForm"),
  nameInput: document.querySelector("#nameInput"),
  emailInput: document.querySelector("#emailInput"),
  passwordInput: document.querySelector("#passwordInput"),
  authSubmit: document.querySelector("#authSubmit"),
  toggleAuth: document.querySelector("#toggleAuth"),
  authMessage: document.querySelector("#authMessage"),
  userLabel: document.querySelector("#userLabel"),
  logoutButton: document.querySelector("#logoutButton"),
  enableNotify: document.querySelector("#enableNotify"),
  tabs: document.querySelectorAll(".tab-button"),
  tagFilter: document.querySelector("#tagFilter"),
  searchInput: document.querySelector("#searchInput"),
  viewTitle: document.querySelector("#viewTitle"),
  viewSubtitle: document.querySelector("#viewSubtitle"),
  cardForm: document.querySelector("#cardForm"),
  contentInput: document.querySelector("#contentInput"),
  tagsInput: document.querySelector("#tagsInput"),
  importanceInput: document.querySelector("#importanceInput"),
  nextReviewInput: document.querySelector("#nextReviewInput"),
  todayCount: document.querySelector("#todayCount"),
  dueCount: document.querySelector("#dueCount"),
  visibleCount: document.querySelector("#visibleCount"),
  cardList: document.querySelector("#cardList"),
  template: document.querySelector("#cardTemplate"),
};

const viewText = {
  today: ["今日复习", "今天需要看到的内容会在这里排好。"],
  due: ["到点提醒", "已经到提醒时间的内容会优先出现。"],
  upcoming: ["未来计划", "还没到时间的内容可以提前查看。"],
  all: ["全部记忆", "所有未归档内容都在这里。"],
  archived: ["归档内容", "已经收起的内容可以恢复或删除。"],
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || "请求失败");
  }
  return payload;
}

function setAuthMessage(message, isError = false) {
  els.authMessage.textContent = message;
  els.authMessage.style.color = isError ? "var(--rose)" : "var(--muted)";
}

function showApp(user) {
  state.user = user;
  els.authView.classList.add("hidden");
  els.appView.classList.remove("hidden");
  els.userLabel.textContent = user.email;
}

function showAuth() {
  state.user = null;
  els.authView.classList.remove("hidden");
  els.appView.classList.add("hidden");
}

function toDatetimeLocal(date) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function setDefaultReviewTime() {
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  tomorrow.setHours(8, 0, 0, 0);
  els.nextReviewInput.value = toDatetimeLocal(tomorrow);
}

function formatDate(iso) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}

function relativeDue(iso) {
  const now = Date.now();
  const then = new Date(iso).getTime();
  const diffHours = Math.round((then - now) / 36e5);
  if (diffHours < -24) return `逾期 ${Math.abs(Math.round(diffHours / 24))} 天`;
  if (diffHours < 0) return "已经到点";
  if (diffHours < 24) return `${diffHours || 1} 小时后`;
  return `${Math.round(diffHours / 24)} 天后`;
}

function importanceText(level) {
  if (level === 3) return "重要";
  if (level === 1) return "轻量";
  return "普通";
}

function updateViewText() {
  const [title, subtitle] = viewText[state.view] || viewText.today;
  els.viewTitle.textContent = title;
  els.viewSubtitle.textContent = subtitle;
  els.tabs.forEach((button) => button.classList.toggle("active", button.dataset.view === state.view));
}

function renderTags() {
  const current = els.tagFilter.value;
  els.tagFilter.innerHTML = '<option value="">全部标签</option>';
  for (const tag of state.tags) {
    const option = document.createElement("option");
    option.value = tag.name;
    option.textContent = `${tag.name} (${tag.count})`;
    els.tagFilter.append(option);
  }
  els.tagFilter.value = current;
}

function renderCards() {
  els.cardList.innerHTML = "";
  els.visibleCount.textContent = state.cards.length;

  if (state.cards.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "这里暂时没有内容。";
    els.cardList.append(empty);
    return;
  }

  for (const card of state.cards) {
    const node = els.template.content.firstElementChild.cloneNode(true);
    node.dataset.id = card.id;
    node.querySelector("h3").textContent = card.title;
    node.querySelector(".card-meta").textContent = `${relativeDue(card.nextReviewAt)} · ${formatDate(card.nextReviewAt)} · 已复习 ${card.reviewCount} 次`;
    node.querySelector(".card-content").textContent = card.content;

    const pill = node.querySelector(".importance-pill");
    pill.textContent = importanceText(card.importance);
    pill.classList.toggle("high", card.importance === 3);
    pill.classList.toggle("low", card.importance === 1);

    const tagRow = node.querySelector(".tag-row");
    for (const tag of card.tags) {
      const tagNode = document.createElement("span");
      tagNode.textContent = tag;
      tagRow.append(tagNode);
    }

    const isArchived = card.status === "archived";
    node.querySelector('[data-action="archive"]').classList.toggle("hidden", isArchived);
    node.querySelector('[data-action="restore"]').classList.toggle("hidden", !isArchived);
    node.querySelector('[data-action="remembered"]').classList.toggle("hidden", isArchived);
    node.querySelector('[data-action="fuzzy"]').classList.toggle("hidden", isArchived);
    node.querySelector('[data-action="forgotten"]').classList.toggle("hidden", isArchived);
    els.cardList.append(node);
  }
}

function queryStringFor(view) {
  const params = new URLSearchParams({ view });
  if (state.query) params.set("q", state.query);
  if (state.tag) params.set("tag", state.tag);
  return params.toString();
}

async function loadCards() {
  updateViewText();
  const payload = await api(`/api/cards?${queryStringFor(state.view)}`);
  state.cards = payload.cards;
  renderCards();
  await loadCounts();
}

async function loadCounts() {
  const [today, due, tags] = await Promise.all([
    api("/api/cards?view=today"),
    api("/api/cards?view=due"),
    api("/api/tags"),
  ]);
  els.todayCount.textContent = today.cards.length;
  els.dueCount.textContent = due.cards.length;
  state.tags = tags.tags;
  renderTags();
}

async function submitAuth(event) {
  event.preventDefault();
  setAuthMessage("");
  const payload = {
    email: els.emailInput.value.trim(),
    password: els.passwordInput.value,
  };
  if (state.mode === "register") {
    payload.name = els.nameInput.value.trim();
  }
  try {
    const result = await api(`/api/auth/${state.mode === "register" ? "register" : "login"}`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    showApp(result.user);
    setDefaultReviewTime();
    await loadCards();
  } catch (error) {
    setAuthMessage(error.message, true);
  }
}

function toggleAuthMode() {
  state.mode = state.mode === "login" ? "register" : "login";
  const isRegister = state.mode === "register";
  els.nameInput.classList.toggle("hidden", !isRegister);
  els.authSubmit.textContent = isRegister ? "注册并进入" : "登录";
  els.toggleAuth.textContent = isRegister ? "已有账号，去登录" : "创建新账号";
  els.passwordInput.autocomplete = isRegister ? "new-password" : "current-password";
  setAuthMessage("");
}

async function submitCard(event) {
  event.preventDefault();
  const content = els.contentInput.value.trim();
  if (!content) return;
  await api("/api/cards", {
    method: "POST",
    body: JSON.stringify({
      content,
      tags: els.tagsInput.value,
      importance: Number(els.importanceInput.value),
      nextReviewAt: els.nextReviewInput.value,
    }),
  });
  els.contentInput.value = "";
  els.tagsInput.value = "";
  els.importanceInput.value = "2";
  setDefaultReviewTime();
  state.view = "all";
  await loadCards();
}

async function handleCardAction(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const card = event.target.closest(".memory-card");
  const id = card?.dataset.id;
  const action = button.dataset.action;
  if (!id) return;

  if (["remembered", "fuzzy", "forgotten"].includes(action)) {
    await api(`/api/cards/${id}/review`, {
      method: "POST",
      body: JSON.stringify({ result: action }),
    });
  } else if (action === "archive") {
    await api(`/api/cards/${id}/archive`, { method: "POST" });
  } else if (action === "restore") {
    await api(`/api/cards/${id}/restore`, { method: "POST" });
  } else if (action === "delete") {
    const ok = window.confirm("确认删除这条内容？");
    if (!ok) return;
    await api(`/api/cards/${id}`, { method: "DELETE" });
  }
  await loadCards();
}

async function pollDueReminders() {
  if (!state.user) return;
  try {
    const payload = await api("/api/reminders/due");
    for (const card of payload.cards) {
      if (state.notifySeen.has(card.id)) continue;
      state.notifySeen.add(card.id);
      if ("Notification" in window && Notification.permission === "granted") {
        new Notification("记忆提醒", {
          body: card.title,
          tag: `memory-${card.id}`,
        });
      }
    }
  } catch (_) {
    return;
  }
}

async function init() {
  setDefaultReviewTime();
  els.authForm.addEventListener("submit", submitAuth);
  els.toggleAuth.addEventListener("click", toggleAuthMode);
  els.cardForm.addEventListener("submit", submitCard);
  els.cardList.addEventListener("click", handleCardAction);

  els.tabs.forEach((button) => {
    button.addEventListener("click", async () => {
      state.view = button.dataset.view;
      await loadCards();
    });
  });

  let searchTimer;
  els.searchInput.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(async () => {
      state.query = els.searchInput.value.trim();
      await loadCards();
    }, 250);
  });

  els.tagFilter.addEventListener("change", async () => {
    state.tag = els.tagFilter.value;
    await loadCards();
  });

  els.enableNotify.addEventListener("click", async () => {
    if (!("Notification" in window)) {
      window.alert("当前浏览器不支持系统通知。");
      return;
    }
    const permission = await Notification.requestPermission();
    els.enableNotify.textContent = permission === "granted" ? "通知已开启" : "开启通知";
  });

  els.logoutButton.addEventListener("click", async () => {
    await api("/api/auth/logout", { method: "POST" });
    showAuth();
  });

  try {
    const payload = await api("/api/auth/me");
    if (payload.user) {
      showApp(payload.user);
      await loadCards();
    } else {
      showAuth();
    }
  } catch (_) {
    showAuth();
  }

  window.setInterval(pollDueReminders, 60_000);
  pollDueReminders();
}

init();
