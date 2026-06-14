<script>
  // One Litter-Robot 4: live status + remote control. `robot` is the snapshot
  // from /api/robots; `run(path, body)` POSTs a command and the parent swaps in
  // the returned post-command snapshot. `busy` disables controls mid-command.
  let { robot, busy = false, error = null, run } = $props();

  const NIGHT_LIGHT_MODES = ["OFF", "ON", "AUTO"];
  const BRIGHTNESS_LABELS = { 25: "Low", 50: "Medium", 100: "High" };
  const PANEL_LEVELS = ["LOW", "MEDIUM", "HIGH"];

  const confirmRun = (msg, path, body) => {
    if (confirm(msg)) run(path, body);
  };

  // A drawer that's filling / litter that's running low gets a warning color.
  const fillClass = (pct, { invert = false } = {}) => {
    const low = invert ? pct >= 80 : pct <= 20;
    const warn = invert ? pct >= 60 : pct <= 40;
    return low ? "bad" : warn ? "warn" : "";
  };

  const dotClass = $derived(
    !robot.online
      ? "bad"
      : ["CLEAN_CYCLE", "EMPTY_CYCLE", "CAT_DETECTED", "PAUSED"].includes(robot.status)
        ? "busy"
        : robot.status === "READY"
          ? "ok"
          : "bad"
  );

  // Surface only faults that aren't in a healthy/clear state.
  const faults = $derived(
    [
      ["Globe motor", robot.faults?.globe_motor, ["NONE", "FAULT_CLEAR"]],
      ["USB", robot.faults?.usb, ["NONE", "CLEAR"]],
    ]
      .filter(([, v, ok]) => v && !ok.includes(v))
      .map(([label, v]) => `${label}: ${v}`)
      .concat(
        robot.faults?.wifi && robot.faults.wifi.includes("FAULT")
          ? [`Wi-Fi: ${robot.faults.wifi}`]
          : []
      )
  );
</script>

<div class="robot" class:offline={!robot.online}>
  <div class="robot-head">
    <div class="robot-title">
      <span class="status-dot {dotClass}"></span>
      <div>
        <h3>{robot.name}</h3>
        <span class="robot-model">{robot.model}</span>
      </div>
    </div>
    <div class="robot-status">
      {robot.online ? robot.status_text : "Offline"}
      {#if !robot.power_on && robot.online}<br /><small>Powered off</small>{/if}
    </div>
  </div>

  <div class="robot-meta">
    <span>Last weight <b>{robot.pet_weight_lbs ? robot.pet_weight_lbs.toFixed(2) : "—"} lbs</b></span>
    <span>Cycles <b>{robot.cycle_count}</b></span>
    <span>Power <b>{robot.power_type}</b></span>
    <span>Scoops saved <b>{robot.scoops_saved}</b></span>
  </div>

  <div class="gauge">
    <div class="gauge-label"><span>Litter level</span><b>{robot.litter_level}%</b></div>
    <div class="bar"><div class="fill {fillClass(robot.litter_level)}" style="width:{robot.litter_level}%"></div></div>
  </div>
  <div class="gauge">
    <div class="gauge-label"><span>Waste drawer</span><b>{robot.waste_drawer_level}%{robot.is_drawer_full ? " · FULL" : ""}</b></div>
    <div class="bar"><div class="fill {fillClass(robot.waste_drawer_level, { invert: true })}" style="width:{robot.waste_drawer_level}%"></div></div>
  </div>

  <div class="robot-actions">
    <button class="btn sm" disabled={busy} onclick={() => run("clean", {})}>Clean now</button>
    <button class="btn sm secondary" disabled={busy} onclick={() => confirmRun("Reset the robot? This clears faults and may start a cycle — make sure the globe is clear.", "reset", {})}>Reset</button>
    {#if robot.firmware_update_available}
      <button class="btn sm secondary" disabled={busy} onclick={() => confirmRun("Start a firmware update now?", "firmware-update", {})}>Update firmware</button>
    {/if}
  </div>

  <div class="ctl-list">
    <div class="ctl-row">
      <span class="ctl-label">Wait time</span>
      <select class="inline-select" disabled={busy} value={robot.wait_time_minutes}
        onchange={(e) => run("wait-time", { minutes: Number(e.currentTarget.value) })}>
        {#each robot.valid_wait_times as w}<option value={w}>{w} min</option>{/each}
      </select>
    </div>

    <div class="ctl-row">
      <span class="ctl-label">Night light</span>
      <span style="display:flex; gap:6px;">
        <select class="inline-select" disabled={busy} value={robot.night_light_mode}
          onchange={(e) => run("night-light", { mode: e.currentTarget.value, brightness: robot.night_light_brightness })}>
          {#each NIGHT_LIGHT_MODES as m}<option value={m}>{m[0] + m.slice(1).toLowerCase()}</option>{/each}
        </select>
        <select class="inline-select" disabled={busy || robot.night_light_mode === "OFF"} value={robot.night_light_brightness}
          onchange={(e) => run("night-light", { mode: robot.night_light_mode, brightness: Number(e.currentTarget.value) })}>
          {#each robot.valid_brightness_levels as b}<option value={b}>{BRIGHTNESS_LABELS[b] ?? b}</option>{/each}
        </select>
      </span>
    </div>

    <div class="ctl-row">
      <span class="ctl-label">Panel brightness</span>
      <select class="inline-select" disabled={busy} value={robot.panel_brightness ?? "HIGH"}
        onchange={(e) => run("panel-brightness", { level: { LOW: 25, MEDIUM: 50, HIGH: 100 }[e.currentTarget.value] })}>
        {#each PANEL_LEVELS as l}<option value={l}>{l[0] + l.slice(1).toLowerCase()}</option>{/each}
      </select>
    </div>

    <div class="ctl-row">
      <span class="ctl-label has-tip" data-tip="Disables the physical buttons on the unit." tabindex="0">Panel lock</span>
      <button class="toggle" class:on={robot.panel_lock_enabled} disabled={busy}
        onclick={() => run("panel-lock", { locked: !robot.panel_lock_enabled })}>
        {robot.panel_lock_enabled ? "On" : "Off"}
      </button>
    </div>

    <div class="ctl-row">
      <span class="ctl-label has-tip" data-tip="The automatic litter-refill accessory. Off disables/removes it ({robot.hopper_status ?? 'not installed'})." tabindex="0">LitterHopper</span>
      <button class="toggle" class:on={!robot.is_hopper_removed} disabled={busy}
        onclick={() => run("hopper", { removed: !robot.is_hopper_removed })}>
        {robot.is_hopper_removed ? "Off" : "On"}
      </button>
    </div>

    <div class="ctl-row">
      <span class="ctl-label has-tip" data-tip="Turns the unit off. There's no remote power-on — once it's off you must press the physical Power button on the unit to turn it back on." tabindex="0">Power</span>
      <button class="toggle" class:on={robot.power_on} disabled={busy}
        onclick={() => robot.power_on
          ? confirmRun(`Turn ${robot.name} OFF?\n\nThe Litter-Robot 4 has NO remote power-on — once it's off you'll have to press the physical Power button on the unit to turn it back on. It also won't clean until then.`, "power", { on: false })
          : run("power", { on: true })}>
        {robot.power_on ? "On" : "Off"}
      </button>
    </div>
  </div>

  {#if faults.length}
    <div class="robot-faults">
      {#each faults as f}<span class="fault-badge">⚠ {f}</span>{/each}
    </div>
  {/if}
  {#if error}<div class="robot-error">{error}</div>{/if}
</div>
