<script>
  // Live status + remote control. Mounted by App only when controls are enabled
  // (the "Live" tab). It shows the cached /api/robots snapshot instantly, then
  // streams live updates over Server-Sent Events (backed by the Whisker
  // WebSocket). If the stream drops it falls back to polling until it recovers.
  import { onMount } from "svelte";
  import { fetchJSON } from "./api.js";
  import LitterRobotControls from "./LitterRobotControls.svelte";
  import FeederControls from "./FeederControls.svelte";

  const POLL_MS = 20000;

  // `live` is bound by the parent so it can show the stream state as a dot on the
  // Live tab. true while the SSE stream is connected.
  let { live = $bindable(false) } = $props();

  let robots = $state([]);
  let loading = $state(true);
  let loadError = $state(null);
  let busy = $state({}); // robot id -> command in flight
  let errors = $state({}); // robot id -> last command error

  const anyBusy = $derived(Object.values(busy).some(Boolean));

  // Replace the whole list (initial load + polling fallback).
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

  // Upsert a single robot snapshot (live push or command response).
  function mergeRobot(snap) {
    const i = robots.findIndex((r) => r.id === snap.id);
    if (i === -1) robots = [...robots, snap];
    else {
      const next = robots.slice();
      next[i] = snap;
      robots = next;
    }
    loading = false;
    loadError = null;
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
        mergeRobot(result.robot);
        if (!result.ok) {
          errors = { ...errors, [robot.id]: "The robot didn't accept that command." };
        }
        // Settings the unit reflects with a lag (panel lock, hopper) will arrive
        // as a live push shortly; nothing else to do.
        return result; // { ok, robot } — callers that need to react can await it
      } catch (err) {
        console.error(`[catdash] command ${path} failed:`, err);
        errors = { ...errors, [robot.id]: err.message };
        return undefined; // signals failure to awaiting callers
      } finally {
        busy = { ...busy, [robot.id]: false };
      }
    };
  }

  // --- Live stream with polling fallback ---
  let fallbackTimer = null;
  function startFallback() {
    if (fallbackTimer) return;
    fallbackTimer = setInterval(() => {
      if (!anyBusy) load();
    }, POLL_MS);
  }
  function stopFallback() {
    if (fallbackTimer) clearInterval(fallbackTimer);
    fallbackTimer = null;
  }

  function openStream() {
    const es = new EventSource("/api/robots/stream");
    es.onopen = () => {
      live = true;
      stopFallback(); // real-time now; no need to poll
    };
    es.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.robot) mergeRobot(msg.robot);
      } catch (err) {
        console.warn("[catdash] bad stream message:", err);
      }
    };
    es.onerror = () => {
      // EventSource auto-reconnects; meanwhile poll so data doesn't go stale.
      live = false;
      startFallback();
    };
    return es;
  }

  onMount(() => {
    load(); // instant cached snapshot — shown until the stream delivers
    const es = openStream();
    return () => {
      es.close();
      stopFallback();
    };
  });
</script>

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
