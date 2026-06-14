<script>
  import { fetchJSON, rangeToStart, ACTIVITY_CATEGORIES } from "./lib/api.js";
  import { loadPersisted, savePersisted } from "./lib/persist.js";
  import { themeState } from "./lib/theme.svelte.js";
  import Controls from "./lib/Controls.svelte";
  import Robots from "./lib/Robots.svelte";
  import Cards from "./lib/Cards.svelte";
  import WeightChart from "./lib/WeightChart.svelte";
  import UsageChart from "./lib/UsageChart.svelte";
  import FeederChart from "./lib/FeederChart.svelte";
  import ActivityFilter from "./lib/ActivityFilter.svelte";
  import ActivityTable from "./lib/ActivityTable.svelte";

  let pets = $state([]);
  // UI selections persist to localStorage (see the $effect below). Add a new
  // option here with loadPersisted(...) and a savePersisted line to persist it.
  let petId = $state(loadPersisted("petId", ""));
  let range = $state(loadPersisted("range", "all"));
  let activityTypes = $state(
    loadPersisted("activityTypes", ACTIVITY_CATEGORIES.map((c) => c.key))
  );
  let ready = $state(false);

  // Whether the remote-control surface exists (CONTROLS_ENABLED on the server).
  // Gates the "Live" tab; resolved once at load via /api/config.
  let controlsEnabled = $state(false);
  let configLoaded = $state(false);
  let activeTab = $state(loadPersisted("tab", "live"));
  // Bound from <Robots>: true while its live stream is connected. Shown as a dot
  // on the Live tab. Reset when we leave the tab (the stream closes then).
  let streamLive = $state(false);

  // The "Live" tab only exists when control is enabled; the history views are
  // split out of one long scroll into Trends (cards + charts) and Activity.
  const TABS = $derived([
    ...(controlsEnabled ? [{ key: "live", label: "Live" }] : []),
    { key: "trends", label: "Trends" },
    { key: "activity", label: "Activity" },
  ]);

  $effect(() => {
    savePersisted("petId", petId);
    savePersisted("range", range);
    savePersisted("activityTypes", activityTypes);
    savePersisted("tab", activeTab);
  });

  $effect(() => {
    // Once we know whether controls exist, fall back to a valid tab if the
    // saved one isn't available (e.g. "live" saved but controls are off).
    if (configLoaded && !TABS.some((t) => t.key === activeTab)) {
      activeTab = TABS[0].key;
    }
  });

  $effect(() => {
    // <Robots> only streams while it's mounted (the Live tab); off the tab the
    // stream is closed, so the dot shouldn't claim "live".
    if (activeTab !== "live") streamLive = false;
  });

  // Keep <html data-theme> in sync with the resolved theme (preference or, for
  // "system", the live OS setting) so the CSS variables follow along.
  $effect(() => {
    document.documentElement.dataset.theme = themeState.resolved;
  });

  let status = $state("");
  let statusError = $state(false);
  let collecting = $state(false);

  // Each section fetches independently: one failing endpoint shows an inline
  // panel error instead of blanking the whole dashboard.
  let sections = $state({
    weights: { data: null, error: null },
    usage: { data: null, error: null },
    food: { data: null, error: null },
    activities: { data: null, error: null },
    stats: { data: null, error: null },
  });

  const subtitle = $derived(
    pets.length === 1
      ? `${pets[0].name}'s weight, litter-box & feeder history`
      : "Weight, litter-box & feeder history"
  );

  const footnote = $derived.by(() => {
    const s = sections.stats.data;
    if (!s) return "";
    const last = s.weight.last ? new Date(s.weight.last).toLocaleString() : "—";
    return `${s.weight.count} weight readings · ${s.usage.total_cycles} cycles · ${s.feeder.feedings} feedings · latest reading ${last}`;
  });

  async function loadSection(key, url) {
    try {
      sections[key] = { data: await fetchJSON(url), error: null };
    } catch (err) {
      console.error(`[catdash] failed to load "${key}" from ${url}:`, err);
      sections[key] = { data: sections[key].data, error: err.message };
    }
  }

  async function refresh() {
    const qs = new URLSearchParams();
    const start = rangeToStart(range);
    if (start) qs.set("start", start);
    const petQs = petId ? `&pet_id=${encodeURIComponent(petId)}` : "";

    status = "Loading…";
    statusError = false;
    await Promise.all([
      loadSection("weights", `/api/weights?${qs}${petQs}`),
      loadSection("usage", `/api/usage?${qs}`),
      loadSection("food", `/api/food?${qs}`),
      loadSection("stats", `/api/stats?${petId ? `pet_id=${encodeURIComponent(petId)}` : ""}`),
      loadActivities(),
    ]);
    const failed = Object.values(sections).filter((s) => s.error).length;
    status = failed ? `${failed} section${failed === 1 ? "" : "s"} failed to load` : "";
    statusError = failed > 0;
  }

  // Activity feed loads separately so toggling category chips refetches only it.
  async function loadActivities() {
    if (activityTypes.length === 0) {
      sections.activities = { data: [], error: null };
      return;
    }
    const qs = new URLSearchParams();
    const start = rangeToStart(range);
    if (start) qs.set("start", start);
    qs.set("limit", "300");
    qs.set("types", activityTypes.join(","));
    await loadSection("activities", `/api/activities?${qs}`);
  }

  async function loadPets() {
    try {
      pets = await fetchJSON("/api/pets");
      if (pets.length === 1) petId = pets[0].id;
    } catch (err) {
      console.error("[catdash] failed to load pets:", err);
      status = `Error: ${err.message}`;
      statusError = true;
    } finally {
      ready = true;
    }
  }

  // Ask the server whether remote control is enabled, so we know whether to show
  // the Live tab. configLoaded flips regardless of outcome so the UI never hangs.
  async function loadConfig() {
    try {
      controlsEnabled = !!(await fetchJSON("/api/config")).controls_enabled;
    } catch (err) {
      console.warn("[catdash] /api/config failed:", err);
    } finally {
      configLoaded = true;
    }
  }

  // Collection runs for several seconds — too long to hold an HTTP response
  // open — so the POST only *starts* it (returning immediately) and we poll
  // /api/refresh/status until the background run finishes. The endpoint is named
  // /api/refresh, not /api/collect, because ad blockers cancel POSTs to a
  // "/collect" path (it matches analytics-beacon filters), which silently breaks
  // the button. The POST is short-lived, so a stale keep-alive failure is retried
  // a couple times on a fresh connection (collection is idempotent).
  async function startCollect(retries = 2) {
    for (let attempt = 0; ; attempt++) {
      try {
        return await fetch("/api/refresh", { method: "POST" });
      } catch (err) {
        console.warn(
          `[catdash] collect POST network error (attempt ${attempt + 1}/${retries + 1}):`,
          err
        );
        if (attempt >= retries) throw err;
        await new Promise((r) => setTimeout(r, 250));
      }
    }
  }

  // Poll until the background collection finishes; returns its result.
  async function awaitCollection({ tries = 80, intervalMs = 1500 } = {}) {
    for (let i = 0; i < tries; i++) {
      await new Promise((r) => setTimeout(r, intervalMs));
      let s;
      try {
        s = await fetchJSON("/api/refresh/status");
      } catch (err) {
        console.warn("[catdash] collect status poll failed, retrying:", err);
        continue; // transient; keep waiting
      }
      if (!s.running) {
        return s.last_error ? { ok: false, error: s.last_error } : s.last_result;
      }
    }
    throw new Error("collection timed out");
  }

  async function collectNow() {
    collecting = true;
    status = "Collecting from Whisker…";
    statusError = false;
    try {
      const res = await startCollect();
      if (res.status === 429) {
        // Manual collections have a server-side cooldown (see /api/refresh);
        // a recent run means the data is already fresh.
        const body = await res.json().catch(() => null);
        const mins = Math.max(1, Math.ceil((body?.retry_after_seconds ?? 60) / 60));
        status = `Collected recently — try again in ${mins} min`;
        statusError = true;
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const result = await awaitCollection();
      if (result && result.ok) {
        status = `Collected: ${result.weights_new} weights, ${result.activities_new} events, ${result.feedings_new} feedings`;
        await loadPets();
        await refresh();
      } else {
        console.error("[catdash] collection finished with an error:", result);
        status = `Collection failed: ${(result && result.error) || "unknown"}`;
        statusError = true;
      }
    } catch (err) {
      console.error("[catdash] collection request failed:", err);
      status = `Collection failed: ${err.message}`;
      statusError = true;
    } finally {
      collecting = false;
    }
  }

  $effect(() => {
    // Re-fetch whenever the pet or range selection changes (after the initial
    // pet load has settled petId, so startup fetches only once).
    void petId;
    void range;
    if (ready) refresh();
  });

  $effect(() => {
    // Refetch just the activity feed when the category filter changes.
    void activityTypes;
    if (ready) loadActivities();
  });

  loadPets();
  loadConfig();
</script>

<header class="topbar">
  <div class="brand">
    <span class="logo">🐈‍⬛</span>
    <div>
      <h1>Catdash</h1>
      <p class="sub">{subtitle}</p>
    </div>
  </div>
  <div class="actions">
    <span class="status" class:error={statusError}>{status}</span>
    <button class="btn" onclick={collectNow} disabled={collecting}>Collect now</button>
  </div>
</header>

{#if configLoaded}
  <nav class="tabs">
    {#each TABS as t}
      <button class="tab" class:active={activeTab === t.key} onclick={() => (activeTab = t.key)}>
        {#if t.key === "live"}<span class="tab-dot" class:on={streamLive}></span>{/if}{t.label}
      </button>
    {/each}
  </nav>

  {#if activeTab === "live"}
    <Robots bind:live={streamLive} />
  {:else if activeTab === "trends"}
    <Controls {pets} bind:petId bind:range />

    {#if sections.stats.data}
      <Cards stats={sections.stats.data} />
    {:else if sections.stats.error}
      <div class="panel-error">Stats failed to load: {sections.stats.error}</div>
    {/if}

    <section class="panel">
      <div class="panel-head">
        <h2>Weight</h2>
        <span class="hint">Weigh-in trend (median per bucket) · 7-day average · raw weigh-ins</span>
      </div>
      {#if sections.weights.data}
        <WeightChart weights={sections.weights.data} />
      {:else if sections.weights.error}
        <div class="panel-error">Weights failed to load: {sections.weights.error}</div>
      {/if}
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>Usage</h2>
        <span class="hint">Clean cycles &amp; weigh-ins per day</span>
      </div>
      {#if sections.usage.data}
        <UsageChart usage={sections.usage.data} />
      {:else if sections.usage.error}
        <div class="panel-error">Usage failed to load: {sections.usage.error}</div>
      {/if}
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>Feeder</h2>
        <span class="hint">Food dispensed (bucketed by range) &amp; hopper level over time</span>
      </div>
      {#if sections.food.data}
        <FeederChart food={sections.food.data} />
      {:else if sections.food.error}
        <div class="panel-error">Feeder data failed to load: {sections.food.error}</div>
      {/if}
    </section>
  {:else if activeTab === "activity"}
    <Controls {pets} bind:petId bind:range />

    <section class="panel">
      <div class="panel-head">
        <h2>Recent activity</h2>
        <ActivityFilter bind:selected={activityTypes} />
      </div>
      {#if sections.activities.data}
        <ActivityTable activities={sections.activities.data} />
      {:else if sections.activities.error}
        <div class="panel-error">Activity failed to load: {sections.activities.error}</div>
      {/if}
    </section>
  {/if}
{/if}

<footer class="foot">
  <span>{footnote}</span>
</footer>
