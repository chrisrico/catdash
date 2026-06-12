<script>
  import { fetchJSON, rangeToStart, ACTIVITY_CATEGORIES } from "./lib/api.js";
  import { loadPersisted, savePersisted } from "./lib/persist.js";
  import { themeState } from "./lib/theme.svelte.js";
  import Controls from "./lib/Controls.svelte";
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
  let showRaw = $state(loadPersisted("showRaw", false));
  let activityTypes = $state(
    loadPersisted("activityTypes", ACTIVITY_CATEGORIES.map((c) => c.key))
  );
  let ready = $state(false);

  $effect(() => {
    savePersisted("petId", petId);
    savePersisted("range", range);
    savePersisted("showRaw", showRaw);
    savePersisted("activityTypes", activityTypes);
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
      status = `Error: ${err.message}`;
      statusError = true;
    } finally {
      ready = true;
    }
  }

  async function collectNow() {
    collecting = true;
    status = "Collecting from Whisker…";
    statusError = false;
    try {
      const res = await fetch("/api/collect", { method: "POST" });
      const body = await res.json();
      if (body.ok) {
        status = `Collected: ${body.weights_new} weights, ${body.activities_new} events, ${body.feedings_new} feedings`;
        await loadPets();
        await refresh();
      } else {
        status = `Collection failed: ${body.error || "unknown"}`;
        statusError = true;
      }
    } catch (err) {
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

<Controls {pets} bind:petId bind:range bind:showRaw />

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
    <WeightChart weights={sections.weights.data} {showRaw} />
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

<footer class="foot">
  <span>{footnote}</span>
</footer>
