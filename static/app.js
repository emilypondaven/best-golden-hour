const app  = document.getElementById("app");
const foot = document.getElementById("foot");
const LOCATION_KEY = "first-light-location";

// Remembered between visits, so GPS and the paid reverse lookup run once
// rather than on every page load.
function savedLocation() {
  try { return JSON.parse(localStorage.getItem(LOCATION_KEY)); }
  catch { return null; }
}
function rememberLocation(loc) {
  where = loc;
  try { localStorage.setItem(LOCATION_KEY, JSON.stringify(loc)); } catch {}
}

let where = savedLocation(), ratings = {};
// Which phase the UI is currently showing - rate() logs against it.
let currentPhase = "sunrise";

// Score bands, described as the sky they predict.
const BANDS = [
  { min: 80, name: "Full colour",  css: "linear-gradient(180deg,#ffcf7a,#ff7a59 55%,#7d3a63)" },
  { min: 60, name: "Some colour",  css: "linear-gradient(180deg,#f6c07a,#d98a52 60%,#4a3a55)" },
  { min: 35, name: "Pale",         css: "linear-gradient(180deg,#a8b6cd,#7b8aa6 60%,#414d68)" },
  { min: 0,  name: "Flat grey",    css: "linear-gradient(180deg,#5b6472,#434a56 60%,#333944)" },
];
const band = s => BANDS.find(b => s >= b.min);

// ---- theme toggle: light = sunrise, dark = sunset ----
const THEME_KEY = "first-light-theme";
const SUNRISE_ICON = `<path d="M12 2v7"/><path d="m4.93 10.93 1.41 1.41"/><path d="M2 18h2"/><path d="M20 18h2"/><path d="m19.07 10.93-1.41 1.41"/><path d="M22 22H2"/><path d="m17 8-5 5-5-5"/><path d="M16 18a4 4 0 0 0-8 0"/>`;
const SUNSET_ICON  = `<path d="M12 9V2"/><path d="m4.93 10.93 1.41 1.41"/><path d="M2 18h2"/><path d="M20 18h2"/><path d="m19.07 10.93-1.41 1.41"/><path d="M22 22H2"/><path d="m7 8 5-5 5 5"/><path d="M16 18a4 4 0 0 0-8 0"/>`;

const themeToggle = document.getElementById("theme-toggle");
const themeIcon = document.getElementById("theme-icon");
const isLightTheme = t => t === "sunrise" || t === "day";
const phaseForTheme = t => t === "sunset" || t === "night" ? "sunset" : "sunrise";

function paintThemeToggle(theme) {
  const light = isLightTheme(theme);
  themeIcon.innerHTML = light ? SUNRISE_ICON : SUNSET_ICON;
  const label = `Switch to ${light ? "sunset (dark)" : "sunrise (light)"} mode`;
  themeToggle.setAttribute("aria-label", label);
  themeToggle.title = label;
}

function setTheme(theme) {
  document.body.dataset.theme = theme;
  paintThemeToggle(theme);
}

themeToggle.onclick = () => {
  const next = isLightTheme(document.body.dataset.theme) ? "sunset" : "sunrise";
  localStorage.setItem(THEME_KEY, next);
  setTheme(next);
  if (currentData) render(currentData);
};

let currentData = null;

// GPS + the Google reverse lookup. Runs only when there's nothing stored,
// or when the user asks again - both cost something, so don't repeat them.
function useMyLocation() {
  if (!navigator.geolocation) {
    app.innerHTML = `<p class="note">Your browser doesn't support geolocation.
      Search for somewhere below instead.</p>`;
    return;
  }
  app.innerHTML = `<p class="note">Finding your location&hellip;</p>`;
  foot.textContent = "";
  navigator.geolocation.getCurrentPosition(async pos => {
    const lat = +pos.coords.latitude.toFixed(2);
    const lon = +pos.coords.longitude.toFixed(2);
    let place = null;
    try {
      const r = await fetch(`/api/whereami?lat=${lat}&lon=${lon}`);
      if (r.ok) place = (await r.json()).place;
    } catch (e) { console.error(e); }   // a missing name is cosmetic
    rememberLocation({ lat, lon, place });
    load();
  }, err => {
    app.innerHTML = `<p class="note">Couldn't get your location — permission denied
      or unavailable. <button onclick="useMyLocation()">Try again</button>,
      or search for somewhere below.</p>`;
    console.error(err);
  }, { timeout: 8000, maximumAge: 300000 });
}

// Free geocoder, so searching never spends the Google key. The label comes
// back with the result, so no reverse lookup is needed either.
const form = document.getElementById("search");
const input = document.getElementById("where");
form.onsubmit = async e => {
  e.preventDefault();
  const typed = input.value.trim();
  if (!typed) return;
  app.innerHTML = `<p class="note">Looking up ${typed}&hellip;</p>`;
  try {
    const r = await fetch(`/api/search?q=${encodeURIComponent(typed)}`);
    if (!r.ok) {
      const { detail } = await r.json();
      app.innerHTML = `<p class="note">${detail}</p>`;
      return;
    }
    rememberLocation(await r.json());
    input.value = "";
    load();
  } catch (err) {
    app.innerHTML = `<p class="note">Lookup failed — try again.</p>`;
    console.error(err);
  }
};

document.getElementById("geolocate").onclick = () => useMyLocation();

function query() {
  return `lat=${where.lat}&lon=${where.lon}`;
}

async function load() {
  if (!where) return useMyLocation();
  app.innerHTML = `<p class="note">Reading the sky&hellip;</p>`;
  foot.textContent = "";
  try {
    const [f, r] = await Promise.all([
      fetch(`/api/forecast?${query()}`),
      fetch("/api/ratings"),
    ]);
    if (f.status === 404) {
      const { detail } = await f.json();
      app.innerHTML = `<p class="note">${detail}</p>`;
      return;
    }
    if (!f.ok) throw new Error(await f.text());
    ratings = Object.fromEntries((await r.json()).map(x => [x.date, x.rating]));
    currentData = await f.json();
    render(currentData);
  } catch (e) {
    app.innerHTML = `<p class="note">No forecast right now — the weather service
      didn't answer. <button onclick="load()">Try again</button></p>`;
    console.error(e);
  }
}

function render(data) {
  const days = [...data.days];
  const theme = applyTheme(data);
  const phase = data.phase || phaseForTheme(theme);
  currentPhase = phase;
  const phaseLabel = phase === "sunrise" ? "Sunrise forecast" : "Sunset forecast";
  const phaseTitle = "Golden Hour";
  const phaseSummary = phase === "sunrise" ? data.sunrise_week_summary : data.sunset_week_summary;
  const bestDate = phase === "sunrise" ? data.best_sunrise_date : data.best_sunset_date;
  const best = days.find(d => d.date === bestDate) || days[0];
  const bestScore = best[phase]?.scores?.score ?? best.score;
  const bestReason = best[phase]?.scores?.reason ?? best.reason;
  const today = new Date().toLocaleDateString("en-CA");

  document.title = "Golden Hour";
  app.innerHTML = `
    <section class="phase-head">
      <p class="eyebrow">${phaseLabel}</p>
      <h1>${phaseTitle}</h1>
    </section>
    <section class="hero">
      <p class="day">${best.date === today ? "Today" : best.weekday}, <em>${best[phase]?.time || best.sunrise.time}</em></p>
      <p class="time">${band(bestScore).name} &middot; scored ${bestScore} of 100</p>
      <p class="reason">${phaseSummary}</p>
    </section>
    <section class="week">${days.map(row.bind(null, data, phase, bestDate, today)).join("")}</section>`;

  document.querySelectorAll(".row").forEach((el, i) => {
    el.style.animationDelay = `${120 + i * 45}ms`;
  });
  app.querySelectorAll("[data-rating]").forEach(b => b.onclick = () => rate(b));

  foot.textContent =
    `${where.place || data.place || `${data.lat}, ${data.lon}`} · `
    + `scored ${data.source === "llm" ? `by ${data.provider || "model"}` : "by rules only"} `
    + `at ${data.generated.slice(11, 16)} · data from Open-Meteo`;
}

function applyTheme(data) {
  const saved = localStorage.getItem(THEME_KEY);
  if (saved) { setTheme(saved); return saved; }
  try {
    const today = new Date().toLocaleDateString("en-CA");
    const todayObj = data.days.find(d => d.date === today) || data.days[0];
    const [y, m, d] = (todayObj.date || data.days[0].date).split("-").map(Number);
    const [srH, srM] = (todayObj.sunrise?.time || "06:00").split(":").map(Number);
    const [ssH, ssM] = (todayObj.sunset?.time || "18:00").split(":").map(Number);
    const sunrise = new Date(y, m - 1, d, srH, srM);
    const sunset  = new Date(y, m - 1, d, ssH, ssM);
    const now = new Date();

    const sunriseStart = new Date(sunrise.getTime() - 60 * 60 * 1000); // -1h
    const sunriseEnd   = new Date(sunrise.getTime() + 2 * 60 * 60 * 1000); // +2h
    const sunsetStart  = new Date(sunset.getTime() - 2 * 60 * 60 * 1000); // -2h
    const sunsetEnd    = new Date(sunset.getTime() + 60 * 60 * 1000); // +1h

    if (now >= sunriseStart && now <= sunriseEnd) {
      setTheme("sunrise");
      return "sunrise";
    } else if (now >= sunsetStart && now <= sunsetEnd) {
      setTheme("sunset");
      return "sunset";
    } else if (now > sunriseEnd && now < sunsetStart) {
      setTheme("day");
      return "day";
    } else {
      setTheme("night");
      return "night";
    }
  } catch (e) {
    // fallback: leave default
    console.warn("Theme application failed:", e);
    return document.body.dataset.theme || "night";
  }
}

function row(data, phase, bestDate, today, d) {
  const past = d.date <= today;
  const rated = ratings[d.date];
  const phaseData = d[phase] || d.sunrise;
  const score = phaseData.scores?.score ?? d.score;
  const reason = phaseData.scores?.reason ?? d.reason;
  return `
    <article class="row ${d.date === bestDate ? "is-best" : ""}">
      <div class="horizon" style="background:${band(score).css}"></div>
      <div>
        <p class="day-name">${d.date === today ? "Today" : d.weekday}</p>
        <p class="meta">${phaseData.time} &middot; ${band(score).name}</p>
        <p class="reason">${reason}</p>
        ${past ? rateHTML(d.date, rated) : ""}
      </div>
      <div class="score">${score}</div>
    </article>`;
}

function rateHTML(date, rated) {
  return `<div class="rate">
    <button data-rating="1" data-date="${date}" aria-pressed="${rated === 1 ? "true" : "false"}">Was good</button>
    <button data-rating="-1" data-date="${date}" aria-pressed="${rated === -1 ? "true" : "false"}">Wasn't</button>
  </div>`;
}

function setRatingState(btn, selectedRating) {
  const buttons = btn.parentElement.querySelectorAll("button");
  buttons.forEach(b => {
    const isSelected = b.dataset.rating === String(selectedRating);
    b.setAttribute("aria-pressed", isSelected ? "true" : "false");
    b.dataset.selected = isSelected ? "true" : "false";
  });
}

async function rate(btn) {
  const { date, rating } = btn.dataset;
  const rateWrap = btn.closest(".rate");
  setRatingState(btn, +rating);
  try {
    const res = await fetch("/api/rate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ date, rating: +rating, lat: where.lat, lon: where.lon, phase: currentPhase }),
    });
    if (!res.ok) throw new Error(await res.text());
    ratings[date] = +rating;
  } catch (e) {
    setRatingState(btn, 0);
    console.error(e);
  }
}

const initialTheme = localStorage.getItem(THEME_KEY) || document.body.dataset.theme || "night";
setTheme(initialTheme);
where ? load() : useMyLocation();