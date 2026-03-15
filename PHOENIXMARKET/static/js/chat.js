// PHOENIXMARKET Support Chat
// Frontend-only chat widget with FAQ fallback and API-ready abstraction.

(function () {
  const root = document.getElementById("pm-chat-root");
  if (!root) return;

  // ---------------------------------------------------------------------------
  // FAQ fallback engine
  // ---------------------------------------------------------------------------
  const faqSupportEngine = (() => {
    const entries = [
      {
        keywords: ["sign in", "login", "log in", "giriş"],
        reply:
          "To sign in, use the “Sign In” button in the top navigation bar. Enter your email and password, then click **Sign in**. If you don’t have an account yet, choose **Sign Up** instead.",
      },
      {
        keywords: ["sign up", "register", "kayıt"],
        reply:
          "You can create a new PHOENIXMARKET account from the **Sign Up** page. Provide your email and a secure password, confirm it, and submit the form.",
      },
      {
        keywords: ["forgot", "reset", "şifre", "password"],
        reply:
          "Use the **Forgot password** link on the Sign In page to start a password reset. In this demo, the UI is ready but email sending will be added later.",
      },
      {
        keywords: ["cart", "sepet"],
        reply:
          "Your cart is always available via the **Cart** icon in the top navigation. Use **Add to cart** on any product card, then open the cart to adjust quantities or remove items.",
      },
      {
        keywords: ["checkout", "order", "buy", "purchase"],
        reply:
          "After adding items to your cart, open the **Cart** page and click **Proceed to checkout**. On the Checkout page, fill in your billing information and confirm your order.",
      },
      {
        keywords: ["account", "profile", "dashboard"],
        reply:
          "Your account area is available from the **Account** link in the navbar once you’re signed in. There you can see profile information and, in future versions, your orders and settings.",
      },
      {
        keywords: ["seller", "sell", "listings"],
        reply:
          "A seller dashboard is planned for managing your own listings and sales. In this demo, the UI is being prepared; full seller tools will be available in a later release.",
      },
      {
        keywords: ["contact", "support", "help", "destek"],
        reply:
          "You can reach PHOENIXMARKET support via the contact section on the homepage footer using email or Discord. This chat assistant is here for quick guidance on using the marketplace.",
      },
    ];

    function getAnswer(message) {
      const text = (message || "").toLowerCase();
      for (const entry of entries) {
        if (entry.keywords.some((k) => text.includes(k))) {
          return entry.reply;
        }
      }
      return (
        "I’m not fully sure about that yet. For detailed or account-specific questions, please contact support via email or Discord in the footer, and we’ll help you directly."
      );
    }

    return { getAnswer };
  })();

  // ---------------------------------------------------------------------------
  // Chat service abstraction (ready for real AI API)
  // ---------------------------------------------------------------------------
  const supportChatService = (() => {
    const apiEnabledAttr = document.body.getAttribute("data-chat-api-enabled");
    const apiEnabled = apiEnabledAttr === "true";

    async function sendMessage(userMessage) {
      if (apiEnabled) {
        try {
          const resp = await fetch("/api/chat", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ message: userMessage }),
          });

          if (resp.ok) {
            const data = await resp.json();
            if (data && data.answer) {
              return { text: data.answer, source: "api" };
            }
          }
          // If server reports AI not configured or any non-OK status,
          // fall back to local FAQ below.
        } catch (e) {
          // Network or server error -> fallback.
          console.warn("AI chat request failed, using FAQ fallback.", e);
        }
      }

      // FAQ fallback (default when API is disabled or errors occur).
      const answer = faqSupportEngine.getAnswer(userMessage);
      return {
        text: answer,
        source: apiEnabled ? "ai+faq" : "faq-only",
      };
    }

    return { sendMessage, apiEnabled };
  })();

  // ---------------------------------------------------------------------------
  // Local storage for chat history
  // ---------------------------------------------------------------------------
  const STORAGE_KEY = "phoenixmarket_chat_history";

  function loadHistory() {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  function saveHistory(messages) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
    } catch {
      // ignore storage errors
    }
  }

  // ---------------------------------------------------------------------------
  // DOM creation helpers
  // ---------------------------------------------------------------------------
  function createEl(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text) el.textContent = text;
    return el;
  }

  // ---------------------------------------------------------------------------
  // Chat widget UI
  // ---------------------------------------------------------------------------
  let isOpen = false;
  let isSending = false;
  let messages = loadHistory();

  const launcher = createEl(
    "button",
    "fixed bottom-4 right-4 z-40 flex h-12 w-12 items-center justify-center rounded-full bg-gradient-to-r from-brandRed to-brandOrange text-black shadow-xl shadow-black/50 focus:outline-none focus:ring-2 focus:ring-brandOrange/80 md:bottom-6 md:right-6"
  );
  launcher.setAttribute("aria-label", "Open PHOENIXMARKET support chat");
  launcher.innerHTML =
    '<span class="text-xl font-semibold select-none">?</span><span class="sr-only">Support chat</span>';

  const windowWrapper = createEl(
    "div",
    "fixed bottom-20 right-4 z-40 hidden w-[calc(100%-2rem)] max-w-sm md:bottom-24 md:right-6"
  );

  const windowPanel = createEl(
    "div",
    "flex h-96 flex-col rounded-2xl border border-slate-800 bg-slate-950/95 shadow-2xl shadow-black/70 backdrop-blur"
  );

  const header = createEl(
    "div",
    "flex items-center justify-between border-b border-slate-800 px-4 py-3"
  );
  const headerTitleWrap = createEl("div");
  const headerTitle = createEl(
    "p",
    "text-xs font-semibold uppercase tracking-[0.2em] text-brandOrange",
    "Support"
  );
  const headerSub = createEl(
    "p",
    "mt-0.5 text-xs text-slate-300",
    "PHOENIXMARKET Assistant"
  );
  headerTitleWrap.appendChild(headerTitle);
  headerTitleWrap.appendChild(headerSub);

  const headerActions = createEl("div", "flex items-center gap-2");
  const headerStatus = createEl(
    "span",
    "inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-300"
  );
  headerStatus.innerHTML =
    '<span class="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse"></span><span>Online</span>';

  const headerClose = createEl(
    "button",
    "ml-1 text-xs text-slate-400 hover:text-slate-200"
  );
  headerClose.textContent = "×";
  headerClose.setAttribute("aria-label", "Close support chat");

  headerActions.appendChild(headerStatus);
  headerActions.appendChild(headerClose);

  header.appendChild(headerTitleWrap);
  header.appendChild(headerActions);

  const messageContainer = createEl(
    "div",
    "flex-1 space-y-2 overflow-y-auto px-3 py-3 text-xs"
  );

  const quickReplies = createEl(
    "div",
    "flex flex-wrap gap-2 px-3 pb-2 text-[11px]"
  );

  const quickReplyOptions = [
    "How do I sign in?",
    "How do I place an order?",
    "Where is my cart?",
    "How do I contact support?",
  ];

  quickReplyOptions.forEach((label) => {
    const btn = createEl(
      "button",
      "rounded-full border border-slate-700 bg-slate-900/80 px-3 py-1 text-slate-300 hover:border-brandOrange hover:text-brandOrange"
    );
    btn.textContent = label;
    btn.addEventListener("click", () => {
      input.value = label;
      handleSend();
    });
    quickReplies.appendChild(btn);
  });

  const inputWrapper = createEl(
    "div",
    "border-t border-slate-800 px-3 py-2 text-xs"
  );
  const form = createEl("form", "flex items-end gap-2");
  const input = createEl(
    "textarea",
    "max-h-24 min-h-[40px] flex-1 resize-none rounded-lg border border-slate-700 bg-slate-900/80 px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:border-brandOrange focus:outline-none",
    ""
  );
  input.setAttribute("rows", "1");
  input.setAttribute("placeholder", "Ask about sign-in, orders, checkout...");

  const sendBtn = createEl(
    "button",
    "inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full bg-gradient-to-r from-brandRed to-brandOrange text-black disabled:opacity-60 disabled:cursor-not-allowed"
  );
  sendBtn.innerHTML =
    '<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4.5 19.5l15-7.5-15-7.5 3 7.5-3 7.5z" /></svg>';

  form.appendChild(input);
  form.appendChild(sendBtn);
  inputWrapper.appendChild(form);

  windowPanel.appendChild(header);
  windowPanel.appendChild(messageContainer);
  windowPanel.appendChild(quickReplies);
  windowPanel.appendChild(inputWrapper);
  windowWrapper.appendChild(windowPanel);

  root.appendChild(launcher);
  root.appendChild(windowWrapper);

  // ---------------------------------------------------------------------------
  // Rendering
  // ---------------------------------------------------------------------------
  function renderMessages() {
    messageContainer.innerHTML = "";
    messages.forEach((m) => {
      const row = createEl(
        "div",
        "flex " + (m.role === "user" ? "justify-end" : "justify-start")
      );
      const bubble = createEl(
        "div",
        "max-w-[80%] rounded-2xl px-3 py-2 " +
          (m.role === "user"
            ? "bg-brandRed text-black rounded-br-sm"
            : "bg-slate-800 text-slate-100 rounded-bl-sm")
      );
      bubble.textContent = m.text;
      row.appendChild(bubble);
      messageContainer.appendChild(row);
    });
    messageContainer.scrollTop = messageContainer.scrollHeight;
  }

  function addMessage(role, text) {
    messages.push({
      id: Date.now() + "-" + Math.random().toString(16).slice(2),
      role,
      text,
    });
    saveHistory(messages);
    renderMessages();
  }

  function addTypingIndicator() {
    const row = createEl("div", "flex justify-start", "");
    row.id = "pm-chat-typing";
    const bubble = createEl(
      "div",
      "inline-flex items-center gap-1 rounded-2xl bg-slate-800 px-3 py-2 text-[10px] text-slate-300 rounded-bl-sm"
    );
    bubble.innerHTML =
      '<span class="h-1.5 w-1.5 rounded-full bg-slate-400 animate-pulse"></span><span>Assistant is typing…</span>';
    row.appendChild(bubble);
    messageContainer.appendChild(row);
    messageContainer.scrollTop = messageContainer.scrollHeight;
  }

  function removeTypingIndicator() {
    const el = document.getElementById("pm-chat-typing");
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  // Initial welcome message
  if (!messages.length) {
    addMessage(
      "assistant",
      "Hi, I’m the PHOENIXMARKET assistant. I can help you with sign-in, cart, checkout, and general questions about how the marketplace works."
    );
  } else {
    renderMessages();
  }

  // ---------------------------------------------------------------------------
  // Behavior
  // ---------------------------------------------------------------------------
  function openChat() {
    if (isOpen) return;
    isOpen = true;
    windowWrapper.classList.remove("hidden");
    launcher.setAttribute("aria-expanded", "true");
  }

  function closeChat() {
    if (!isOpen) return;
    isOpen = false;
    windowWrapper.classList.add("hidden");
    launcher.setAttribute("aria-expanded", "false");
  }

  launcher.addEventListener("click", () => {
    if (isOpen) closeChat();
    else openChat();
  });

  headerClose.addEventListener("click", closeChat);

  function handleSend() {
    const text = input.value.trim();
    if (!text || isSending) return;

    addMessage("user", text);
    input.value = "";
    isSending = true;
    sendBtn.disabled = true;
    addTypingIndicator();

    supportChatService
      .sendMessage(text)
      .then((res) => {
        removeTypingIndicator();
        addMessage("assistant", res.text);
      })
      .catch(() => {
        removeTypingIndicator();
        addMessage(
          "assistant",
          "Sorry, something went wrong while generating a response. Please try again or contact support via email/Discord."
        );
      })
      .finally(() => {
        isSending = false;
        sendBtn.disabled = false;
      });
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    handleSend();
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  });
})();

