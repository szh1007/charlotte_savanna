/* ============================================================
   可复用组件：交互测验
   契约（后续课程直接照这个写）：

     <div class="quiz" data-quiz></div>
     <script type="application/json" data-quiz-data>
     [ { "q": "题干",
         "options": { "答案键": "显示文本", ... },   // 键是判据，值是呈现；answer 必须匹配某个键
         "answer":  "答案键",
         "explain": "答完后显示的解析" } ]
     </script>
     <script src="../assets/quiz.js"></script>

   为什么数据放 <script type="application/json"> 而不是 data-* 属性：
     属性值要再过一层 HTML 实体/引号转义，任何一处手误（少个引号）都会让
     整个 JSON 静默失效，报错还指不到行。放进 script 块里就是纯文本，
     `tools/check_lessons.py` 能直接抽出原文做 json.loads 校验，报错带行列号。

   行为：
     - 一次只显示一题（working memory 一次装不下四道）
     - 选项顺序**每道题都打乱**：唯一答案若总排在第一个，做题人就不用判断了
     - 选项文本长度应尽量接近，否则长度本身就在提示答案
   设计取舍：解析只讲「判据」，不复述题干 —— 复述会让人把阅读误当成回忆。
   ============================================================ */
(function () {
    "use strict";

    /* 固定种子的伪随机：每次打乱结果一致，便于「明天重做同一题」时对照，
       同时避免每次刷新布局乱跳。种子取自题号，所以每题乱法不同。 */
    function seededShuffle(items, seed) {
        var i;
        var j;
        var t;
        var s = seed;

        for (i = items.length - 1; i > 0; i--) {
            s = (s * 9301 + 49297) % 233280;
            j = Math.floor((s / 233280) * (i + 1));
            t = items[i];
            items[i] = items[j];
            items[j] = t;
        }
        return items;
    }

    function parseQuestions(container) {
        var dataEl = document.querySelector("script[data-quiz-data]");

        if (!dataEl) {
            return { error: "没找到 data-quiz-data 脚本块。" };
        }
        try {
            return { questions: JSON.parse(dataEl.textContent) };
        } catch (err) {
            return { error: "题目数据解析失败：" + err.message };
        }
    }

    function buildQuestion(quizId, question, index, total) {
        var wrap = document.createElement("div");
        wrap.className = "quiz__item";
        wrap.setAttribute("data-question-index", String(index));

        var counter = document.createElement("p");
        counter.className = "lesson-meta";
        counter.textContent = "第 " + (index + 1) + " / " + total + " 题";
        wrap.appendChild(counter);

        var prompt = document.createElement("p");
        prompt.className = "quiz__question";
        prompt.textContent = question.q;
        wrap.appendChild(prompt);

        var entries = Object.keys(question.options).map(function (key) {
            return { key: key, text: question.options[key] };
        });
        seededShuffle(entries, index + 7);

        var list = document.createElement("div");
        list.className = "quiz__options";

        entries.forEach(function (entry) {
            var btn = document.createElement("button");
            btn.type = "button";
            btn.className = "quiz__option";
            btn.setAttribute("data-key", entry.key);
            btn.textContent = entry.text;
            list.appendChild(btn);
        });
        wrap.appendChild(list);

        var feedback = document.createElement("p");
        feedback.className = "quiz__feedback";
        wrap.appendChild(feedback);

        list.addEventListener("click", function (event) {
            var btn = event.target.closest(".quiz__option");
            var correct;
            var text;

            if (!btn || list.getAttribute("data-answered") === "true") {
                return;
            }
            list.setAttribute("data-answered", "true");

            correct = btn.getAttribute("data-key") === question.answer;
            btn.setAttribute("data-state", correct ? "correct" : "wrong");

            /* 答错时把正确选项也标出来。不自动滚动、不高亮变化外的任何东西 ——
               先让用户自己看一眼，免得解析被直接跳过。 */
            if (!correct) {
                list.querySelectorAll(".quiz__option").forEach(function (other) {
                    if (other.getAttribute("data-key") === question.answer) {
                        other.setAttribute("data-state", "correct");
                    }
                });
            }

            text = (correct ? "对。" : "不对。") + " " + (question.explain || "");
            feedback.textContent = text;
            feedback.setAttribute("data-visible", "true");
        });

        wrap.setAttribute("data-quiz-id", quizId);
        return wrap;
    }

    function initQuiz(container, quizIndex) {
        var parsed = parseQuestions(container);
        var questions;
        var i;

        if (parsed.error) {
            container.textContent = parsed.error;
            return;
        }
        questions = parsed.questions;
        if (!Array.isArray(questions) || questions.length === 0) {
            container.textContent = "题目数据是空的。";
            return;
        }
        for (i = 0; i < questions.length; i++) {
            container.appendChild(buildQuestion(quizIndex, questions[i], i, questions.length));
        }
    }

    function init() {
        var quizzes = document.querySelectorAll("[data-quiz]");
        var k;

        for (k = 0; k < quizzes.length; k++) {
            initQuiz(quizzes[k], k);
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
