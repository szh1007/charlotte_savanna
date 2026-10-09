/* ============================================================
   可复用组件：写路径 / 持久化模拟器
   契约（后续课程可以直接改造复用）：

     <div class="sim" data-sim></div>
     <script type="application/json" data-sim-config>
     { "knobs": [ { "id": "1", "label": "旋钮档位名", "note": "一句话说明" }, ... ],
       "initialCommits": 3 }
     </script>
     <script src="../assets/simulator.js"></script>

   模型（三层存储，本组件的核心抽象）：
     ① redo log buffer  进程内存              —— kill 进程就没了，断电更没了
     ② OS page cache    已 write() 给操作系统 —— kill 进程还在，断电没了
     ③ disk             已 fsync() 到物理设备 —— 什么都不怕

   为什么按时间推进而不是按点击推进：要演示的那一档真实语义就是「按时间触发 fsync」，
   用离散 tick 表示一秒，比动画更容易看清「哪一刻差了多少」。

   设计取舍：不追求界面好看，追求**每一步都说得清数据在哪一层** ——
   学员要建立的是「事故时数据停在哪」的心智模型。
   ============================================================ */
(function () {
    "use strict";

    var TICK_MS = 2500;
    var DANGER_MS = 900;   // 超过这个时长还在「等待 fsync」就给个警示色

    function makeEl(tag, className, text) {
        var el = document.createElement(tag);
        if (className) {
            el.className = className;
        }
        if (text !== undefined && text !== null) {
            el.textContent = text;
        }
        return el;
    }

    function readConfig() {
        var el = document.querySelector("script[data-sim-config]");
        var fallback = {
            knobs: [{ id: "1", label: "默认档位", note: "" }],
            initialCommits: 3
        };
        var cfg;

        if (!el) {
            return fallback;
        }
        try {
            cfg = JSON.parse(el.textContent);
        } catch (err) {
            return fallback;
        }
        if (!cfg.knobs || !cfg.knobs.length) {
            return fallback;
        }
        if (typeof cfg.initialCommits !== "number") {
            cfg.initialCommits = fallback.initialCommits;
        }
        return cfg;
    }

    function Simulator(host, config) {
        this.host = host;
        this.config = config;
        this.knob = String(config.knobs[0].id);
        this.tick = 0;
        this.seq = 0;
        this.buffer = [];   // ① 进程内存
        this.cache = [];    // ② OS page cache
        this.disk = [];     // ③ 磁盘
        this.all = [];      // 曾经提交过的一切，只用于事后标记「哪几笔丢了」
        this.lostIds = {};
        this.crashed = false;
        this.build();
        this.reset();
    }

    Simulator.prototype.build = function () {
        var self = this;
        var k;

        this.controls = makeEl("div", "sim__controls");
        this.knobButtons = this.config.knobs.map(function (knob) {
            var btn = makeEl("button", "sim__knob", knob.label);
            btn.type = "button";
            btn.setAttribute("data-knob", knob.id);
            btn.addEventListener("click", function () {
                self.setKnob(knob.id);
            });
            return btn;
        });
        for (k = 0; k < this.knobButtons.length; k++) {
            this.controls.appendChild(this.knobButtons[k]);
        }

        this.note = makeEl("p", "sim__note");
        this.controls.appendChild(this.note);

        this.grid = makeEl("div", "sim__layers");
        this.status = makeEl("div", "sim__status");

        this.actions = makeEl("div", "sim__actions");
        this.commitBtn = this.addAction("提交一笔事务", null, function () {
            self.commit();
        });
        this.killBtn = this.addAction("kill 掉数据库进程", "sim__action--danger", function () {
            self.crash(true);
        });
        this.powerBtn = this.addAction("机房断电", "sim__action--danger", function () {
            self.crash(false);
        });
        this.addAction("重新开始", null, function () {
            self.reset();
        });

        this.host.appendChild(this.controls);
        this.host.appendChild(this.grid);
        this.host.appendChild(this.actions);
        this.host.appendChild(this.status);
    };

    Simulator.prototype.addAction = function (label, extraClass, handler) {
        var btn = makeEl("button", extraClass ? "sim__action " + extraClass : "sim__action", label);
        btn.type = "button";
        btn.addEventListener("click", handler);
        this.actions.appendChild(btn);
        return btn;
    };

    /* 建一层并**立即挂进网格**，返回那一层的格子容器。
       这里刻意采用「构建即接线」的写法：先前那版把建好的层先存进变量、事后再 appendChild，
       结果漏掉了接线那一步 —— 层和格子都建出来了，却没挂到网格上，页面上只剩一个空网格。
       少一个中间态，就少一次漏接线。 */
    Simulator.prototype.buildLayer = function (title, subtitle) {
        var box = makeEl("div", "sim__layer");
        box.appendChild(makeEl("div", "sim__layer-head", title));
        box.appendChild(makeEl("div", "sim__layer-sub", subtitle));
        var cells = makeEl("div", "sim__cells");
        box.appendChild(cells);
        this.grid.appendChild(box);
        return cells;
    };

    Simulator.prototype.renderCell = function (cellsEl, id, flags) {
        var cell = makeEl("span", "sim__cell");
        cell.appendChild(makeEl("span", "sim__cell-id", id));
        cell.appendChild(makeEl("span", "sim__cell-flag", flags));
        cellsEl.appendChild(cell);
        return cell;
    };

    Simulator.prototype.render = function () {
        var self = this;
        var bufferCells;
        var cacheCells;
        var diskCells;

        /* 每次重画都整体重建：状态小、重建比增删改更不容易漏步。
           buildLayer 内部已经把层挂进网格，这里只管拿格子往里填。 */
        this.grid.textContent = "";
        bufferCells = this.buildLayer("① 进程内存 redo log buffer", "kill 进程就消失");
        cacheCells = this.buildLayer("② OS page cache", "write() 过，进程崩不丢、断电丢");
        diskCells = this.buildLayer("③ 磁盘", "fsync() 过，什么都不怕");

        this.buffer.forEach(function (tx) {
            var cell = self.renderCell(bufferCells, tx.id, "✗");
            cell.classList.add("sim__cell--doomed");
        });

        this.cache.forEach(function (tx) {
            var cell = self.renderCell(cacheCells, tx.id, tx.synced ? "✓" : "…");
            if (!tx.synced) {
                cell.classList.add("sim__cell--pending");
            }
        });

        this.disk.forEach(function (tx) {
            var cell = self.renderCell(diskCells, tx.id, "✓");
            if (self.lostIds[tx.id]) {
                cell.classList.add("sim__cell--survivor");
            }
        });

        if (!this.buffer.length) {
            bufferCells.appendChild(makeEl("span", "sim__empty", "（空）"));
        }
        if (!this.cache.length) {
            cacheCells.appendChild(makeEl("span", "sim__empty", this.crashed ? "（已被断电清空）" : "（空）"));
        }
        if (!this.disk.length) {
            diskCells.appendChild(makeEl("span", "sim__empty", "（空）"));
        }
    };

    /* 已写进 page cache、但还没被 fsync 带走的事务数 —— 就是「最坏会丢多少」。 */
    Simulator.prototype.pendingCount = function () {
        var pending = this.cache.filter(function (tx) {
            return !tx.synced;
        });
        return pending.length;
    };

    Simulator.prototype.setKnob = function (id) {
        this.knob = String(id);
        this.updateKnobUI();
        this.refreshStatus();
    };

    Simulator.prototype.updateKnobUI = function () {
        var self = this;
        var found;

        this.knobButtons.forEach(function (btn) {
            var on = btn.getAttribute("data-knob") === self.knob;
            btn.classList.toggle("sim__knob--active", on);
            btn.setAttribute("aria-pressed", on ? "true" : "false");
        });
        found = this.config.knobs.filter(function (knob) {
            return String(knob.id) === self.knob;
        })[0];
        this.note.textContent = found ? found.note : "";
    };

    Simulator.prototype.reset = function () {
        var i;
        var times = this.config.initialCommits || 3;

        this.tick = 0;
        this.seq = 0;
        this.buffer = [];
        this.cache = [];
        this.disk = [];
        this.all = [];
        this.lostIds = {};
        this.crashed = false;
        this.updateKnobUI();

        for (i = 0; i < times; i++) {
            this.commit(true);
        }
        this.render();
        this.refreshStatus();
    };

    Simulator.prototype.commit = function (silent) {
        var tx;

        if (this.crashed) {
            this.lostIds = {};
            this.crashed = false;
        }

        this.seq += 1;
        tx = { id: "T" + this.seq, at: this.tick, synced: false };
        this.all.push(tx);

        if (this.knob === "1") {
            /* 每次提交都同步 fsync：当场就到磁盘 */
            tx.synced = true;
            this.disk.push(tx);
        } else {
            /* 提交时只写进进程缓冲，下一个 tick 才 write() 给操作系统 */
            this.buffer.push(tx);
        }

        this.tick += 1;
        this.drain();
        this.writeTick = this.tick;

        if (!silent) {
            this.render();
            this.refreshStatus();
        }
    };

    /* 把一个 tick 的搬运做完：① → ② → ③。
       留在缓冲里的，就是「差在这一 tick 之内」的那批 —— 也是最会丢的那批。 */
    Simulator.prototype.drain = function () {
        var self = this;
        var stillBuffered = [];

        this.buffer.forEach(function (tx) {
            if (tx.at >= self.tick) {
                stillBuffered.push(tx);
            } else if (self.knob === "1") {
                tx.synced = true;
                self.disk.push(tx);
            } else {
                self.cache.push(tx);
            }
        });
        this.buffer = stillBuffered;
    };

    Simulator.prototype.onTick = function () {
        this.tick += 1;
        this.drain();
        if (this.knob === "2") {
            /* 一档里「每秒一次」的那个后台 fsync */
            this.cache.forEach(function (tx) {
                tx.synced = true;
            });
        }
        this.render();
        this.refreshStatus();
    };

    Simulator.prototype.refreshStatus = function () {
        var pending;

        if (this.crashed) {
            return;
        }
        pending = this.pendingCount();
        if (this.knob === "1") {
            this.status.textContent =
                "每次提交都同步 fsync —— 磁盘上 " + this.disk.length + " 笔，" +
                "进程内存和 page cache 里都不留东西。最坏情况：一笔都不丢。";
        } else {
            this.status.textContent =
                "磁盘上 " + this.disk.length + " 笔；page cache 里还有 " + pending +
                " 笔已经 write() 但还没 fsync —— 这就是最坏情况下会丢的量。";
        }
    };

    Simulator.prototype.crash = function (isProcessCrash) {
        var self = this;
        var survivors = this.disk.slice();
        var lostInBuffer = this.buffer.length;
        var lostInCache = 0;

        /* ① 进程内存：无论怎么摔都没了 */
        this.buffer = [];
        if (isProcessCrash) {
            /* kill 进程：page cache 属于操作系统，活下来 */
            this.cache.forEach(function (tx) {
                survivors.push(tx);
            });
        } else {
            lostInCache = this.cache.length;
        }
        this.cache = [];

        /* 事后标记：磁盘上这批是活下来的 —— 把 id 记下来给 render 上色 */
        this.lostIds = {};
        survivors.forEach(function (tx) {
            self.lostIds[tx.id] = true;
        });
        this.disk = survivors;
        this.crashed = true;

        this.render();
        this.status.textContent =
            (isProcessCrash ? "kill 进程" : "机房断电") +
            " → 活下来 " + survivors.length + " 笔" +
            "（共提交 " + this.all.length + " 笔）。" +
            " 进程内存里丢 " + lostInBuffer + " 笔，page cache 里丢 " + lostInCache + " 笔。" +
            (isProcessCrash
                ? " 注意：kill 进程伤不到 page cache，那是操作系统的地盘。"
                : " 断电把 page cache 一起带走了 —— 只有 fsync 过的才算数。");
    };

    function init() {
        var hosts = document.querySelectorAll("[data-sim]");
        var config = readConfig();
        var k;
        var sim;

        for (k = 0; k < hosts.length; k++) {
            sim = new Simulator(hosts[k], config);
            window.setInterval(function () {
                if (!this.crashed) {
                    this.onTick();
                }
            }.bind(sim), TICK_MS);
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
