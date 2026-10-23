(() => {
  const root = document.getElementById("accountProfilePage");
  if (!root) return;

  const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  const panelTimeZone = root.dataset.appTimezone || "Asia/Yangon";

  function formatDate(value) {
    if (!value) return "—";
    // A date-only value is a calendar date: show it exactly as written.
    const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim());
    if (dateOnly) {
      const year = Number(dateOnly[1]);
      const month = Number(dateOnly[2]);
      const day = Number(dateOnly[3]);
      const parsed = new Date(Date.UTC(year, month - 1, day));
      if (
        Number.isNaN(parsed.getTime())
        || parsed.getUTCFullYear() !== year
        || parsed.getUTCMonth() !== month - 1
        || parsed.getUTCDate() !== day
      ) return "—";
      return `${String(day).padStart(2, "0")} ${monthNames[month - 1]} ${year}`;
    }
    // Timestamps are instants: show them on the panel timezone, the
    // same standard the audit logs use. Slicing the date out of the
    // string would show the UTC date, a day early for evening-UTC
    // timestamps.
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "—";
    return new Intl.DateTimeFormat("en-GB", {
      timeZone: panelTimeZone,
      day: "2-digit",
      month: "short",
      year: "numeric",
    }).format(parsed);
  }

  root.querySelectorAll("[data-profile-date]").forEach((element) => {
    element.textContent = formatDate(element.dataset.profileDate);
  });

  root.querySelector("[data-photo-trigger]")?.addEventListener("click", () => {
    root.querySelector("#acct-avatar-file")?.click();
  });

  const strengthMeter = root.querySelector("[data-password-meter]");
  const strengthLabel = root.querySelector("[data-password-strength]");
  if (strengthMeter && strengthLabel) {
    const updateStrengthLabel = () => {
      const strength = Number(strengthMeter.dataset.strength) || 0;
      const visualStrength = Math.min(4, strength + 1);
      strengthMeter.dataset.kpStrength = String(visualStrength);
      strengthLabel.dataset.kpStrength = String(visualStrength);
      strengthLabel.textContent = ["Too weak", "Weak", "Fair", "Strong", "Strong"][strength];
    };
    updateStrengthLabel();
    new MutationObserver(updateStrengthLabel).observe(strengthMeter, {
      attributes: true,
      attributeFilter: ["data-strength"],
    });
  }

  const tabs = [...root.querySelectorAll("[data-profile-tab]")];
  const sections = [...root.querySelectorAll("[data-profile-section]")];
  function activateTab(sectionId) {
    tabs.forEach((tab) => {
      const active = tab.dataset.profileTab === sectionId;
      tab.classList.toggle("kp-tab--active", active);
      if (active) tab.setAttribute("aria-current", "location");
      else tab.removeAttribute("aria-current");
    });
  }

  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver((entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((left, right) => right.intersectionRatio - left.intersectionRatio)[0];
      if (visible) activateTab(visible.target.id);
    }, { rootMargin: "-18% 0px -62% 0px", threshold: [0, 0.25, 0.5, 0.75] });
    sections.forEach((section) => observer.observe(section));
  }

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => activateTab(tab.dataset.profileTab));
  });

  function renderPasswordToggle(button) {
    const shown = button.getAttribute("aria-pressed") === "true";
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.75");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    const eye = document.createElementNS("http://www.w3.org/2000/svg", "path");
    eye.setAttribute("d", "M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z");
    const pupil = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    pupil.setAttribute("cx", "12");
    pupil.setAttribute("cy", "12");
    pupil.setAttribute("r", "3");
    svg.append(eye, pupil);
    if (shown) {
      const cover = document.createElementNS("http://www.w3.org/2000/svg", "path");
      cover.setAttribute("d", "m3 3 18 18M10.6 5.1A9.4 9.4 0 0 1 12 5c6.4 0 10 7 10 7a16 16 0 0 1-3 3.8M6.2 6.2C3.5 8.1 2 12 2 12s3.6 7 10 7a9.6 9.6 0 0 0 3.1-.5");
      svg.append(cover);
    }
    const label = document.createElement("span");
    label.className = "kp-sr-only";
    label.textContent = shown ? "Hide password" : "Show password";
    button.setAttribute("aria-label", label.textContent);
    button.replaceChildren(svg, label);
  }

  root.querySelectorAll("[data-password-toggle]").forEach((button) => {
    renderPasswordToggle(button);
    button.addEventListener("click", () => renderPasswordToggle(button));
  });

  root.querySelectorAll("form[data-account-profile-form], form[data-account-password-form]").forEach((form) => {
    const submit = form.querySelector('button[type="submit"]');
    const banners = [...root.querySelectorAll("[data-acct-banner]")];
    form.addEventListener("submit", () => {
      form.setAttribute("aria-busy", "true");
    }, true);

    const observer = new MutationObserver(() => {
      const bannerVisible = banners.some((banner) => !banner.hidden);
      const submissionFinished = submit && !submit.querySelector(".acct-spinner");
      if (
        form.getAttribute("aria-busy") !== "false"
        && (bannerVisible || (form.getAttribute("aria-busy") === "true" && submissionFinished))
      ) {
        form.setAttribute("aria-busy", "false");
      }
    });
    observer.observe(form, { attributes: true, childList: true, subtree: true });
    banners.forEach((banner) => observer.observe(banner, { attributes: true }));
  });

  root.dataset.kpReady = "true";
})();
