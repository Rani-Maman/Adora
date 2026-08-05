(function () {
  const API_BASE = (window.ADORA_API_BASE || "").replace(/\/$/, "");

  const MASTERCARD_URL =
    "https://www.mastercard.com/global/en/news-and-trends/Insights/2024/ecommerce-fraud-trends-and-statistics-merchants-need-to-know-in-2024.html";

  const BBC_URL = "https://www.bbc.com/news/business-55420445";

  const STR = {
    en: {
      brandTag: "Shop checker",
      tagline: "Check a shop before you buy",
      placeholder: "https://example.co.il",
      check: "Check",
      checking: "Checking…",
      stat1Value: "$343B+",
      stat1Label: "Projected fraud losses, 2023–2027",
      stat2Value: "$48B+",
      stat2Label: "Global e-commerce fraud, per year",
      stat3Value: "—",
      stat3Label: "Flagged in Adora",
      stat4Value: "~21%",
      stat4Label: "Shopify stores analyzed — fraud-linked",
      bannerSourceText: "Juniper Research · Jan 2024 — full report →",
      bannerSourceAria: "Mastercard — Juniper Research report, January 2024",
      bbcSourceText: "Fakespot · Dec 2020 — BBC report →",
      bbcSourceAria: "BBC — Fakespot Shopify fraud analysis, December 2020",
      flaggedTitle: "Potential dropshipping",
      flaggedIntro: "This URL appears in Adora's database based on automated analysis.",
      pendingTitle: "Not in Adora's database yet",
      pendingBody:
        "Not in Adora's database doesn't mean it is safe or unsafe — it just means Adora hasn't checked it yet. Thank you for adding this URL to Adora.",
      rateLimited: "Please wait before checking another URL.",
      errorGeneric: "Something went wrong. Please try again.",
      disclaimer:
        "Adora doesn't guarantee 100% success; mistakes can always happen.",
      score: "Score",
      evidence: "Evidence",
    },
    he: {
      brandTag: "בודק חנויות",
      tagline: "בדיקת חנות לפני קנייה",
      placeholder: "https://example.co.il",
      check: "בדוק",
      checking: "בודק…",
      stat1Value: "$343B+",
      stat1Label: "הפסדי הונאות צפויים, 2023–2027",
      stat2Value: "$48B+",
      stat2Label: "הונאות e-commerce גלובליות, בשנה",
      stat3Value: "—",
      stat3Label: "סומנו ב-Adora",
      stat4Value: "~21%",
      stat4Label: "חנויות Shopify שנותחו — קשורות להונאה",
      bannerSourceText: "Juniper Research · ינואר 2024 — למאמר המלא →",
      bannerSourceAria: "Mastercard — דוח Juniper Research, ינואר 2024",
      bbcSourceText: "Fakespot · דצמבר 2020 — לכתבה ב-BBC →",
      bbcSourceAria: "BBC — ניתוח Fakespot לחנויות Shopify, דצמבר 2020",
      flaggedTitle: "דרופשיפינג פוטנציאלי",
      flaggedIntro: "כתובת זו מופיעה במאגר Adora על בסיס ניתוח אוטומטי.",
      pendingTitle: "עדיין לא נבדק ב-Adora",
      pendingBody:
        "לא נמצא במאגר Adora לא אומר שהחנות בטוחה או מסוכנת — רק ש-Adora עדיין לא בדקה אותה. תודה שהוספת את הכתובת ל-Adora.",
      rateLimited: "אנא המתן לפני בדיקת כתובת נוספת.",
      errorGeneric: "משהו השתבש. נסה שוב.",
      disclaimer:
        "Adora לא מבטיחה 100% הצלחה; טעויות עלולות לקרות תמיד.",
      score: "ציון",
      evidence: "סימנים",
    },
  };

  let lang = localStorage.getItem("adoraWebLang") || "en";

  const el = (id) => document.getElementById(id);

  function t(key) {
    return STR[lang][key] || STR.en[key] || key;
  }

  function applyLang() {
    document.documentElement.lang = lang === "he" ? "he" : "en";
    document.documentElement.dir = lang === "he" ? "rtl" : "ltr";
    if (el("brandTag")) el("brandTag").textContent = t("brandTag");
    el("tagline").textContent = t("tagline");
    el("urlInput").placeholder = t("placeholder");
    el("checkBtn").textContent = t("check");
    el("langToggle").textContent = lang === "he" ? "EN" : "עב";
    el("stat1Value").textContent = t("stat1Value");
    el("stat1Label").textContent = t("stat1Label");
    el("stat2Value").textContent = t("stat2Value");
    el("stat2Label").textContent = t("stat2Label");
    el("stat3Label").textContent = t("stat3Label");
    el("stat4Value").textContent = t("stat4Value");
    el("stat4Label").textContent = t("stat4Label");
    document.querySelectorAll(".stat-attribution--mc").forEach(function (link) {
      link.href = MASTERCARD_URL;
      link.setAttribute("aria-label", t("bannerSourceAria"));
    });
    document.querySelectorAll(".source-tooltip--mc").forEach(function (tip) {
      tip.textContent = t("bannerSourceText");
    });
    document.querySelectorAll(".stat-attribution--bbc").forEach(function (link) {
      link.href = BBC_URL;
      link.setAttribute("aria-label", t("bbcSourceAria"));
    });
    document.querySelectorAll(".source-tooltip--bbc").forEach(function (tip) {
      tip.textContent = t("bbcSourceText");
    });
    el("footerDisclaimer").textContent = t("disclaimer");
  }

  function showResult(html, type) {
    const box = el("result");
    box.className = "result visible " + (type || "");
    box.innerHTML = html;
  }

  function hideResult() {
    const box = el("result");
    box.className = "result";
    box.innerHTML = "";
  }

  function setLoading(on) {
    el("spinner").classList.toggle("visible", on);
    el("spinner").textContent = t("checking");
    el("checkBtn").disabled = on;
  }

  async function loadStats() {
    if (!API_BASE) return;
    try {
      const res = await fetch(API_BASE + "/check/stats");
      if (!res.ok) return;
      const data = await res.json();
      if (data.domains_in_database != null) {
        el("stat3Value").textContent = String(data.domains_in_database);
      }
    } catch (_) {
      /* banner keeps placeholder */
    }
  }

  async function checkUrl() {
    const url = el("urlInput").value.trim();
    if (!url) return;

    if (!API_BASE) {
      showResult("<p>" + t("errorGeneric") + "</p>", "error");
      return;
    }

    hideResult();
    setLoading(true);

    try {
      const res = await fetch(API_BASE + "/check/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      const data = await res.json();

      if (res.status === 429) {
        const sec = data.retry_after_seconds || 60;
        showResult(
          "<h2>" +
            t("rateLimited") +
            "</h2><p>" +
            (lang === "he" ? "נסה שוב בעוד " : "Try again in ") +
            sec +
            (lang === "he" ? " שניות." : " seconds.") +
            "</p>",
          "error"
        );
        return;
      }

      if (!res.ok || data.status === "error") {
        showResult(
          "<h2>" + t("errorGeneric") + "</h2><p>" + (data.message || "") + "</p>",
          "error"
        );
        return;
      }

      if (data.status === "flagged") {
        let html =
          "<h2>" +
          t("flaggedTitle") +
          "</h2>" +
          "<p>" +
          t("flaggedIntro") +
          "</p>" +
          "<p><strong>" +
          t("score") +
          ":</strong> " +
          data.score +
          "</p>";

        if (data.evidence && data.evidence.length) {
          html += "<p><strong>" + t("evidence") + ":</strong></p><ul>";
          data.evidence.forEach(function (item) {
            html += "<li>" + escapeHtml(String(item)) + "</li>";
          });
          html += "</ul>";
        }

        html +=
          '<p class="disclaimer">' +
          escapeHtml(data.disclaimer || t("disclaimer")) +
          "</p>";
        showResult(html, "flagged");
        return;
      }

      if (data.status === "pending") {
        showResult(
          "<h2>" +
            t("pendingTitle") +
            "</h2>" +
            "<p>" +
            escapeHtml(data.message || t("pendingBody")) +
            "</p>" +
            '<p class="disclaimer">' +
            escapeHtml(data.disclaimer || t("disclaimer")) +
            "</p>",
          "pending"
        );
      }
    } catch (err) {
      showResult("<h2>" + t("errorGeneric") + "</h2>", "error");
    } finally {
      setLoading(false);
    }
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  el("langToggle").addEventListener("click", function () {
    lang = lang === "he" ? "en" : "he";
    localStorage.setItem("adoraWebLang", lang);
    applyLang();
  });

  el("checkForm").addEventListener("submit", function (e) {
    e.preventDefault();
    checkUrl();
  });

  applyLang();
  loadStats();
})();
