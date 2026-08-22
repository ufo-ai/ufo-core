// App bridge client: connects a framed homepage to the portal shell over postMessage.
// Matches the prototype bridge contract (docs/apps-prototype-contracts.md, Contract 1): one
// generic `call` verb over the shell's endpoint table; ufoRead/ufoWrite/ufoChat are conveniences
// built on it. Prototype: posts to "*" and trusts the shell; origin pinning is deferred (RFC 0039
// debt).
(function () {
  var pending = new Map();
  var counter = 0;
  var initCb = null;
  var initData = null;
  var openCb = null;

  function send(obj) {
    // The page is doubly framed: the portal pane frames the sites frame page, which frames this
    // page. The shell listens on the portal's own window — the top — and accepts sources one
    // level under its pane, so this posts past the frame page in between.
    window.top.postMessage(obj, "*");
  }

  function request(fields) {
    var id = "r" + ++counter;
    return new Promise(function (resolve, reject) {
      pending.set(id, { resolve: resolve, reject: reject });
      fields.id = id;
      send(fields);
    });
  }

  var readyTimer = null;

  window.addEventListener("message", function (event) {
    var msg = event.data;
    if (!msg || typeof msg !== "object" || typeof msg.ufo !== "string") return;
    if (msg.ufo === "init") {
      if (readyTimer !== null) {
        clearTimeout(readyTimer);
        readyTimer = null;
      }
      if (initData) return;
      initData = msg;
      if (initCb) initCb(msg);
      return;
    }
    if (msg.ufo === "data") {
      var entry = pending.get(msg.id);
      if (entry) {
        pending.delete(msg.id);
        // The shell relays the surface's answer verbatim: status, body text, and the refusal and
        // session-fault headers. ufoCall keeps its promise shape — parsed JSON or an Error.
        if (msg.ok) {
          try {
            entry.resolve(msg.body === "" ? null : JSON.parse(msg.body));
          } catch (e) {
            entry.reject(new Error("unreadable reply"));
          }
        } else {
          entry.reject(new Error(msg.error || msg.body || "request refused"));
        }
        return;
      }
      var refused = streams.get(msg.id);
      if (refused && !msg.ok) {
        streams.delete(msg.id);
        if (refused.end) refused.end(msg.error || msg.body || "stream refused");
      }
      return;
    }
    if (msg.ufo === "open") {
      // The pane's open target changed while the page stands — the live half of init's `open`.
      if (openCb) openCb(msg.target == null ? null : msg.target);
      return;
    }
    if (msg.ufo === "frame") {
      var stream = streams.get(msg.id);
      if (stream && stream.frame) stream.frame(msg.event, msg.data);
      return;
    }
    if (msg.ufo === "end") {
      var ended = streams.get(msg.id);
      streams.delete(msg.id);
      if (ended && ended.end) ended.end(msg.error);
    }
  });

  window.ufoCall = function (method, path, body) {
    if (typeof method !== "string" || !method) {
      return Promise.reject(new Error("ufoCall needs a method"));
    }
    if (typeof path !== "string" || !path) {
      return Promise.reject(new Error("ufoCall needs a path"));
    }
    var fields = { ufo: "call", method: method, path: path };
    if (body !== undefined) fields.body = body;
    return request(fields);
  };
  window.ufoRead = function (path) {
    return window.ufoCall("GET", path);
  };
  window.ufoWrite = function (kind, name, spec) {
    if (typeof kind !== "string" || !kind) {
      return Promise.reject(new Error("ufoWrite needs a string kind"));
    }
    if (typeof name !== "string" || !name) {
      return Promise.reject(new Error("ufoWrite needs a string name"));
    }
    if (typeof spec !== "object" || spec === null || Array.isArray(spec)) {
      return Promise.reject(new Error("ufoWrite needs a spec object"));
    }
    if (!initData) {
      return Promise.reject(new Error("ufoWrite needs init — call it from onInit"));
    }
    return window.ufoCall("POST", "objects/" + encodeURIComponent(kind), {
      name: name,
      spec: spec,
      agent: initData.agentId,
    });
  };
  window.ufoChat = function (agent, conversation, text) {
    if (typeof agent !== "string" || !agent) {
      return Promise.reject(new Error("ufoChat needs an agent id"));
    }
    if (typeof conversation !== "string" || !conversation) {
      return Promise.reject(new Error("ufoChat needs a conversation id or 'new'"));
    }
    if (typeof text !== "string" || !text.trim()) {
      return Promise.reject(new Error("ufoChat needs message text"));
    }
    return window.ufoCall(
      "POST",
      "agents/" + agent + "/chat?conversation=" + encodeURIComponent(conversation),
      text
    );
  };
  var streams = new Map();

  window.ufoStream = function (path, handlers) {
    var id = "s" + ++counter;
    streams.set(id, handlers || {});
    send({ ufo: "call", id: id, method: "GET", path: path });
    return {
      close: function () {
        streams.delete(id);
        send({ ufo: "close", id: id });
      },
    };
  };
  window.ufoNavigate = function (to) {
    send({ ufo: "navigate", to: to });
  };
  window.ufoFounded = function (agentId, conversationId, title) {
    send({ ufo: "founded", agent_id: agentId, conversation_id: conversationId, title: title });
  };
  window.onInit = function (cb) {
    if (initData) cb(initData);
    else initCb = cb;
  };
  window.onOpen = function (cb) {
    openCb = cb;
  };

  // The shell attaches its message listener after the pane mounts, so a frame that loads first
  // would lose a single `ready`. Retry until `init` arrives (which clears the timer), then stop;
  // give up after a bounded span so a page opened outside the shell does not spin forever.
  var attempts = 0;
  function announce() {
    send({ ufo: "ready" });
    if (initData || attempts++ >= 30) return;
    readyTimer = setTimeout(announce, 100);
  }
  announce();
})();
