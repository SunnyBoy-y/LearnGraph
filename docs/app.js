(() => {
  const sections = [...document.querySelectorAll(".doc-section")];
  const primaryLinks = [...document.querySelectorAll(".primary-nav a")];
  const toc = document.querySelector("[data-toc]");
  const sidebar = document.querySelector("[data-sidebar]");
  const menuToggle = document.querySelector("[data-menu-toggle]");
  const dialog = document.querySelector("[data-search-dialog]");
  const searchInput = document.querySelector("[data-search-input]");
  const searchResults = document.querySelector("[data-search-results]");
  const toast = document.querySelector("[data-toast]");
  let toastTimer;

  sections.forEach((section) => {
    const link = document.createElement("a");
    link.href = `#${section.id}`;
    link.textContent = section.dataset.title || section.querySelector("h2, h1")?.textContent || section.id;
    toc?.appendChild(link);
  });

  const tocLinks = [...document.querySelectorAll("[data-toc] a")];
  const setActiveSection = (id) => {
    primaryLinks.forEach((link) => link.classList.toggle("is-active", link.hash === `#${id}`));
    tocLinks.forEach((link) => link.classList.toggle("is-active", link.hash === `#${id}`));
  };

  const observer = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (visible) setActiveSection(visible.target.id);
    },
    { rootMargin: "-18% 0px -68% 0px", threshold: [0, 0.1, 0.3] }
  );
  sections.forEach((section) => observer.observe(section));

  menuToggle?.addEventListener("click", () => {
    const isOpen = sidebar?.classList.toggle("is-open");
    menuToggle.setAttribute("aria-expanded", String(Boolean(isOpen)));
  });
  primaryLinks.forEach((link) =>
    link.addEventListener("click", () => {
      sidebar?.classList.remove("is-open");
      menuToggle?.setAttribute("aria-expanded", "false");
    })
  );
  document.addEventListener("click", (event) => {
    if (
      window.innerWidth <= 780 &&
      sidebar?.classList.contains("is-open") &&
      !sidebar.contains(event.target) &&
      !menuToggle?.contains(event.target)
    ) {
      sidebar.classList.remove("is-open");
      menuToggle?.setAttribute("aria-expanded", "false");
    }
  });

  const searchIndex = sections.map((section) => ({
    id: section.id,
    title: section.dataset.title || section.querySelector("h2, h1")?.textContent || section.id,
    text: section.textContent.replace(/\s+/g, " ").trim(),
  }));

  const openSearch = () => {
    dialog?.classList.add("is-open");
    dialog?.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    searchInput.value = "";
    renderSearch("");
    setTimeout(() => searchInput?.focus(), 20);
  };
  const closeSearch = () => {
    dialog?.classList.remove("is-open");
    dialog?.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
  };

  const renderSearch = (query) => {
    if (!searchResults) return;
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized) {
      searchResults.innerHTML = '<p class="search-hint">输入关键词以搜索本文档章节</p>';
      return;
    }
    const hits = searchIndex
      .filter((item) => item.text.toLocaleLowerCase().includes(normalized))
      .sort((a, b) => {
        const aTitle = a.title.toLocaleLowerCase();
        const bTitle = b.title.toLocaleLowerCase();
        const score = (title) =>
          title === normalized ? 3 : title.startsWith(normalized) ? 2 : title.includes(normalized) ? 1 : 0;
        return score(bTitle) - score(aTitle);
      })
      .slice(0, 9);
    if (!hits.length) {
      searchResults.innerHTML = '<p class="search-empty">没有找到匹配章节</p>';
      return;
    }
    searchResults.innerHTML = hits
      .map((item) => {
        const lowerText = item.text.toLocaleLowerCase();
        const index = lowerText.indexOf(normalized);
        const start = Math.max(0, index - 36);
        const excerpt = item.text.slice(start, start + 105);
        return `<a class="search-result" href="#${item.id}"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(excerpt)}…</span></a>`;
      })
      .join("");
    searchResults.querySelectorAll(".search-result").forEach((result) => {
      result.addEventListener("click", closeSearch);
    });
  };

  const escapeHtml = (value) =>
    value.replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    })[character]);

  document.querySelectorAll("[data-open-search]").forEach((button) => button.addEventListener("click", openSearch));
  document.querySelectorAll("[data-close-search]").forEach((button) => button.addEventListener("click", closeSearch));
  searchInput?.addEventListener("input", (event) => renderSearch(event.target.value));
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === "k") {
      event.preventDefault();
      openSearch();
    }
    if (event.key === "Escape") {
      closeSearch();
      sidebar?.classList.remove("is-open");
    }
  });

  const showToast = (message) => {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add("is-visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 1800);
  };

  document.querySelectorAll("[data-copy-code]").forEach((button) => {
    button.addEventListener("click", async () => {
      const code = button.closest(".code-block")?.querySelector("code")?.textContent || "";
      try {
        await navigator.clipboard.writeText(code);
        button.textContent = "已复制";
        showToast("已复制到剪贴板");
        setTimeout(() => (button.textContent = "复制"), 1500);
      } catch {
        showToast("复制失败，请手动选择");
      }
    });
  });

  const animateSequence = (elements, activeClass, button, labels) => {
    let runId = Number(button.dataset.runId || 0) + 1;
    button.dataset.runId = String(runId);
    button.disabled = true;
    elements.forEach((element) => element.classList.remove(activeClass));
    elements.forEach((element, index) => {
      setTimeout(() => {
        if (Number(button.dataset.runId) !== runId) return;
        element.classList.add(activeClass);
        if (index === elements.length - 1) {
          button.disabled = false;
          button.textContent = labels.done;
          showToast(labels.toast);
          setTimeout(() => (button.textContent = labels.idle), 1400);
        }
      }, index * 420);
    });
  };

  const pipelineButton = document.querySelector("[data-run-pipeline]");
  pipelineButton?.addEventListener("click", () =>
    animateSequence(
      [...document.querySelectorAll("[data-demo-pipeline] .media-step")],
      "is-active",
      pipelineButton,
      { idle: "播放一次输入管线", done: "管线已完成", toast: "媒体输入已完成路由与持久化" }
    )
  );

  const disclosureButton = document.querySelector("[data-run-disclosure]");
  disclosureButton?.addEventListener("click", () =>
    animateSequence(
      [...document.querySelectorAll("[data-disclosure-demo] .disclosure-stage")],
      "is-visible",
      disclosureButton,
      { idle: "逐层展开工具上下文", done: "上下文已展开", toast: "能力地图、工具契约与执行轨迹已展开" }
    )
  );

  const revealTargets = document.querySelectorAll(
    ".timeline-item, .principle-grid article, .tool-matrix article, .callout"
  );
  revealTargets.forEach((element) => element.setAttribute("data-reveal", ""));
  const revealObserver = new IntersectionObserver(
    (entries) => entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("is-revealed");
      revealObserver.unobserve(entry.target);
    }),
    { threshold: 0.12 }
  );
  revealTargets.forEach((element) => revealObserver.observe(element));
})();
