<script>
  // Live status + remote control section. Self-gating: it asks /api/config
  // whether controls are enabled and renders NOTHING when they aren't, so the
  // read-only dashboard is unchanged on a default deployment. When enabled it
  // loads /api/robots, polls it for near-live status, and dispatches commands.
  import { onMount } from "svelte";
  import { fetchJSON } from "./api.js";
  import LitterRobotControls from "./LitterRobotControls.svelte";
  import FeederControls from "./FeederControls.svelte";

  const POLL_MS = 20000;

  let enabled = $state(false);
  let robots = $state([]);
  let loading = $state(true);
  let loadError = $state(null);
  let busy = $state({}); // robot id -> command in flight
  let errors = $state({}); // robot id -> last command error

  const anyBusy = $derived(Object.values(busy).some(Boolean));

  async function load() {
    try {
      robots = await fetchJSON("/api/robots");
      loadError = null;
    } catch (err) {
      console.error("[catdash] failed to load robots:", err);
      loadError = err.message;
    } finally {
      loading = false;
    }
  }

  async function postCommand(robot, path, body) {
    const base = robot.kind === "feeder" ? "/api/feeders" : "/api/robots";
    const res = await fetch(`${base}/${robot.id}/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        detail = (await res.json()).detail || detail;
      } catch {}
      throw new Error(detail);
    }
    return res.json(); // { ok, robot }
  }

  function makeRun(robot) {
    return async (path, body) => {
      busy = { ...busy, [robot.id]: true };
      errors = { ...errors, [robot.id]: null };
      try {
        const result = await postCommand(robot, path, body);
        robots = robots.map((r) => (r.id === robot.id ? result.robot : r));
        if (!result.ok) {
          errors = { ...errors, [robot.id]: "The robot didn't accept that command." };
        }
        // Some settings (panel lock, hopper) take several seconds to read back
        // the new value from the unit, so the immediate post-command snapshot can
        // still show the OLD state. Reconcile a few seconds later so the UI
        // corrects itself well before the regular 20s poll would.
        for (const ms of [3500, 8000]) {
          setTimeout(() => {
            if (!anyBusy) load();
          }, ms);
        }
      } catch (err) {
        console.error(`[catdash] command ${path} failed:`, err);
        errors = { ...errors, [robot.id]: err.message };
      } finally {
        busy = { ...busy, [robot.id]: false };
      }
    };
  }

  onMount(() => {
    let timer;
    (async () => {
      try {
        enabled = !!(await fetchJSON("/api/config")).controls_enabled;
      } catch (err) {
        console.warn("[catdash] /api/config failed:", err);
      }
      if (!enabled) {
        loading = false;
        return;
      }
      await load();
      // Poll for near-live status, but never clobber state mid-command.
      timer = setInterval(() => {
        if (!anyBusy) load();
      }, POLL_MS);
    })();
    return () => clearInterval(timer);
  });
</script>

{#if enabled}
  <section class="panel">
    <div class="panel-head">
      <h2>Robots</h2>
      <span class="hint">Live status &amp; remote control · updates every {POLL_MS / 1000}s</span>
    </div>

    {#if loading}
      <div class="empty">Connecting to your robots…</div>
    {:else if loadError}
      <div class="panel-error">Couldn't reach your robots: {loadError}</div>
    {:else if robots.length === 0}
      <div class="empty">No robots found on this account.</div>
    {:else}
      <div class="robot-grid">
        {#each robots as robot (robot.id)}
          {#if robot.kind === "feeder"}
            <FeederControls {robot} busy={!!busy[robot.id]} error={errors[robot.id]} run={makeRun(robot)} />
          {:else}
            <LitterRobotControls {robot} busy={!!busy[robot.id]} error={errors[robot.id]} run={makeRun(robot)} />
          {/if}
        {/each}
      </div>
    {/if}
  </section>
{/if}
