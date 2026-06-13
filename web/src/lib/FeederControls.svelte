<script>
  // One Feeder-Robot: live status + remote control. Same contract as
  // LitterRobotControls — `robot` snapshot, `run(path, body)` command callback.
  import { fmtCups, fmtDateTime } from "./api.js";

  let { robot, busy = false, error = null, run } = $props();

  const cupsLabel = (c) => (c === 0.25 ? "¼ cup" : c === 0.125 ? "⅛ cup" : `${c} cup`);
  const fillClass = (pct) => (pct <= 10 ? "bad" : pct <= 30 ? "warn" : "");

  const dotClass = $derived(!robot.online ? "bad" : robot.power_on ? "ok" : "bad");
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
      {robot.online ? (robot.power_on ? "Online" : "Powered off") : "Offline"}
    </div>
  </div>

  <div class="robot-meta">
    <span>Power <b>{robot.power_type}</b></span>
    {#if robot.next_feeding}
      <span>Next meal <b>{fmtDateTime(robot.next_feeding)}</b></span>
    {:else if robot.gravity_mode_enabled}
      <span><b>Gravity mode</b></span>
    {/if}
    {#if robot.last_feeding}
      <span>Last <b>{robot.last_feeding.name} · {fmtCups(robot.last_feeding.amount_cups)}</b></span>
    {/if}
  </div>

  <div class="gauge">
    <div class="gauge-label"><span>Food level</span><b>{robot.food_level}%</b></div>
    <div class="bar"><div class="fill {fillClass(robot.food_level)}" style="width:{robot.food_level}%"></div></div>
  </div>

  <div class="robot-actions">
    <button class="btn sm" disabled={busy} onclick={() => run("snack", {})}>Give snack</button>
  </div>

  <div class="ctl-list">
    <div class="ctl-row">
      <span class="ctl-label">Meal portion size</span>
      <select class="inline-select" disabled={busy} value={robot.meal_insert_size}
        onchange={(e) => run("meal-insert-size", { cups: Number(e.currentTarget.value) })}>
        {#each robot.valid_meal_insert_sizes as c}<option value={c}>{cupsLabel(c)}</option>{/each}
      </select>
    </div>

    <div class="ctl-row">
      <span class="ctl-label">Gravity mode <small>free-feed when the bowl empties</small></span>
      <button class="toggle" class:on={robot.gravity_mode_enabled} disabled={busy}
        onclick={() => run("gravity-mode", { on: !robot.gravity_mode_enabled })}>
        {robot.gravity_mode_enabled ? "On" : "Off"}
      </button>
    </div>

    <div class="ctl-row">
      <span class="ctl-label">Night light</span>
      <button class="toggle" class:on={robot.night_light_enabled} disabled={busy}
        onclick={() => run("night-light", { on: !robot.night_light_enabled })}>
        {robot.night_light_enabled ? "On" : "Off"}
      </button>
    </div>

    <div class="ctl-row">
      <span class="ctl-label">Panel lock <small>disable the buttons on the unit</small></span>
      <button class="toggle" class:on={robot.panel_lock_enabled} disabled={busy}
        onclick={() => run("panel-lock", { locked: !robot.panel_lock_enabled })}>
        {robot.panel_lock_enabled ? "On" : "Off"}
      </button>
    </div>
  </div>

  {#if error}<div class="robot-error">{error}</div>{/if}
</div>
