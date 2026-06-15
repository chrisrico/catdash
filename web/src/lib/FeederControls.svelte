<script>
  // One Feeder-Robot: live status + remote control. Same contract as
  // LitterRobotControls — `robot` snapshot, `run(path, body)` command callback.
  import { fmtCups, fmtDateTime } from "./api.js";

  let { robot, busy = false, error = null, run } = $props();

  const cupsLabel = (c) => (c === 0.25 ? "¼ cup" : c === 0.125 ? "⅛ cup" : `${c} cup`);
  const fillClass = (pct) => (pct <= 10 ? "bad" : pct <= 30 ? "warn" : "");

  const confirmRun = (msg, path, body) => {
    if (confirm(msg)) run(path, body);
  };

  const dotClass = $derived(!robot.online ? "bad" : robot.power_on ? "ok" : "bad");

  // Schedule helpers (read-only display).
  const fmtMealTime = (h, m) => {
    const d = new Date();
    d.setHours(h ?? 0, m ?? 0, 0, 0);
    return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  };
  const localToday = () => {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  };
  // Only flag a skip that's today or later (past skips are spent).
  const upcomingSkip = (skip) => skip && skip >= localToday();
  const fmtSkip = (skip) => {
    const [y, mo, da] = skip.split("-").map(Number);
    return new Date(y, mo - 1, da).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
  };

  const meals = $derived(robot.schedule?.meals ?? []);

  // Cross off meals actually dispensed today, from the feeder's real feeding
  // history — the server resolves today's dispensed meal numbers from the
  // `feeding_meal` records, so a meal that failed to fire is never crossed off.
  const fedToday = $derived(new Set(robot.fed_meal_numbers ?? []));
  const isDispensed = (m) => m.meal_number != null && fedToday.has(m.meal_number);
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
      <span class="ctl-label has-tip" data-tip="Free-feeds — keeps the bowl full, topping it up about every 6 hours. Enabling dispenses food." tabindex="0">Gravity mode</span>
      <button class="toggle" class:on={robot.gravity_mode_enabled} disabled={busy}
        onclick={() => robot.gravity_mode_enabled
          ? run("gravity-mode", { on: false })
          : confirmRun(`Enable gravity mode on ${robot.name}?\n\nThe feeder will dispense food to keep the bowl full and keep topping it up about every 6 hours, instead of following the schedule.`, "gravity-mode", { on: true })}>
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
      <span class="ctl-label has-tip" data-tip="Disables the physical buttons on the unit." tabindex="0">Panel lock</span>
      <button class="toggle" class:on={robot.panel_lock_enabled} disabled={busy}
        onclick={() => run("panel-lock", { locked: !robot.panel_lock_enabled })}>
        {robot.panel_lock_enabled ? "On" : "Off"}
      </button>
    </div>
  </div>

  {#if meals.length}
    <div class="schedule">
      <div class="schedule-head">
        {robot.schedule?.name ? `Feeding schedule · ${robot.schedule.name}` : "Feeding schedule"}
      </div>
      {#each meals as m}
        <div class="schedule-row" class:dim={m.paused} class:done={isDispensed(m)}>
          <span class="schedule-time">{fmtMealTime(m.hour, m.minute)}</span>
          <span class="schedule-name">{m.name}</span>
          <span class="schedule-days">{m.every_day ? "Every day" : m.days.join(" ")}</span>
          <span class="schedule-portion">{m.cups != null ? fmtCups(m.cups) : `${m.portions}×`}</span>
          {#if m.meal_number != null}
            <span class="schedule-actions">
              <button class="chip" class:active={m.paused} disabled={busy}
                onclick={() => run("pause-meal", { meal_number: m.meal_number, paused: !m.paused })}>
                {m.paused ? "Paused" : "Pause"}
              </button>
              {#if !isDispensed(m)}
                <button class="chip" class:active={upcomingSkip(m.skip)} disabled={busy}
                  onclick={() => run("skip-meal", { meal_number: m.meal_number, skip: !upcomingSkip(m.skip) })}>
                  {upcomingSkip(m.skip) ? `Skip ${fmtSkip(m.skip)} ✕` : "Skip next"}
                </button>
              {/if}
            </span>
          {/if}
        </div>
      {/each}
      <div class="schedule-note">Pause holds a meal indefinitely; Skip drops just the next one.</div>
    </div>
  {/if}

  {#if error}<div class="robot-error">{error}</div>{/if}
</div>

<style>
  .schedule-actions { margin-left: auto; display: inline-flex; gap: 6px; }
  .schedule-actions .chip { cursor: pointer; }
  .schedule-actions .chip:disabled { opacity: 0.5; cursor: progress; }
  .schedule-row.done .schedule-time,
  .schedule-row.done .schedule-name { text-decoration: line-through; opacity: 0.65; }
</style>
