/* ==========================================================================
   quiz.js — 测验组件行为（选择题即时判分 + 口述题自评）
   渐进增强：JS 不可用时页面仍可读（CSS 里答案默认折叠，打印时展开）。
   自评结果存 localStorage，回访时恢复 —— 便于间隔复习。
   ========================================================================== */

(function () {
  "use strict";

  var lessonEl = document.querySelector("[data-lesson]");
  var lessonId = lessonEl ? lessonEl.dataset.lesson : "lesson";
  var STORE_KEY = "ai-interview:" + lessonId;

  function loadState() {
    try {
      return JSON.parse(localStorage.getItem(STORE_KEY)) || {};
    } catch (err) {
      return {};
    }
  }

  function saveState(state) {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(state));
    } catch (err) {
      /* 隐私模式等场景下静默降级为不持久化 */
    }
  }

  var state = loadState();

  /* ---------- 进度小结 ---------- */

  function updateProgress() {
    var box = document.querySelector("[data-progress]");
    if (!box) return;

    var total = document.querySelectorAll(".q[data-qid], .recall[data-qid]").length;
    var done = 0;
    var strong = 0;

    Object.keys(state).forEach(function (k) {
      done += 1;
      if (state[k] === "correct" || state[k] === "hit") strong += 1;
    });

    box.innerHTML =
      "已完成 <b>" + Math.min(done, total) + "</b> / " + total +
      " 题 · 其中 <b>" + strong + "</b> 题答稳了" +
      (strong < done ? " · 剩下 " + (done - strong) + " 题需要重看" : "");
  }

  /* ---------- 选择题 ---------- */

  document.querySelectorAll('.q[data-type="mcq"]').forEach(function (q) {
    var qid = q.dataset.qid;
    var opts = Array.prototype.slice.call(q.querySelectorAll(".opt"));

    function settle(clicked) {
      q.dataset.answered = "true";
      opts.forEach(function (o) {
        o.dataset.state =
          o.dataset.correct === "true" ? "correct" : o === clicked ? "wrong" : "";
      });
    }

    opts.forEach(function (opt) {
      opt.addEventListener("click", function () {
        if (q.dataset.answered === "true") return;
        settle(opt);
        state[qid] = opt.dataset.correct === "true" ? "correct" : "wrong";
        saveState(state);
        updateProgress();
      });

      /* 键盘可达：Enter / Space 等同点击 */
      opt.setAttribute("role", "button");
      opt.setAttribute("tabindex", "0");
      opt.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          opt.click();
        }
      });
    });

    /* 恢复：已答对/答错的题重放当时的标记 */
    if (state[qid]) {
      var picked = opts.filter(function (o) {
        return state[qid] === "correct"
          ? o.dataset.correct === "true"
          : o.dataset.correct !== "true";
      })[0];
      if (picked) settle(picked);
    }
  });

  /* ---------- 口述题（自评） ---------- */

  document.querySelectorAll(".recall[data-qid]").forEach(function (card) {
    var qid = card.dataset.qid;
    var revealBtn = card.querySelector(".btn--reveal");
    var gradeBtns = Array.prototype.slice.call(card.querySelectorAll(".grade-btn"));

    if (revealBtn) {
      revealBtn.addEventListener("click", function () {
        card.dataset.revealed = "true";
        revealBtn.hidden = true;
      });
    }

    gradeBtns.forEach(function (btn) {
      btn.addEventListener("click", function () {
        gradeBtns.forEach(function (b) { b.dataset.picked = "false"; });
        btn.dataset.picked = "true";
        state[qid] = btn.dataset.grade;
        saveState(state);
        updateProgress();
      });
    });

    if (state[qid]) {
      card.dataset.revealed = "true";
      if (revealBtn) revealBtn.hidden = true;
      gradeBtns.forEach(function (b) {
        b.dataset.picked = String(b.dataset.grade === state[qid]);
      });
    }
  });

  updateProgress();
})();
