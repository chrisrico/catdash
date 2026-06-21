<script>
  // One Feeder-Robot: live status + remote control. Same contract as
  // LitterRobotControls — `robot` snapshot, `run(path, body)` command callback.
  import { fmtCups, fmtDateTime } from "./api.js";
  import Modal from "./Modal.svelte";

  let { robot, busy = false, error = null, run } = $props();

  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const pad2 = (n) => String(n).padStart(2, "0");

  // --- Schedule editor (modal) ---
  // draft holds an editable copy of the meals; Save merges it server-side onto
  // the real schedule (preserving each meal's id/pause/skip) via set_schedule.
  let editing = $state(false);
  let draft = $state([]);
  let saving = $state(false);
  let editError = $state(null);
  let keySeq = 0;

  function openEditor() {
    editError = null;
    draft = meals.map((m) => ({
      key: keySeq++,
      meal_number: m.meal_number,
      name: m.name ?? "",
      time: `${pad2(m.hour ?? 0)}:${pad2(m.minute ?? 0)}`,
      portions: m.portions ?? 1,
      days: [...(m.days ?? [])],
      paused: !!m.paused,
    }));
    editing = true;
  }

  const addMeal = () =>
    (draft = [...draft, { key: keySeq++, meal_number: null, name: "Meal", time: "12:00", portions: 1, days: [...DAYS], paused: false }]);
  const removeMeal = (key) => (draft = draft.filter((d) => d.key !== key));
  const toggleDay = (d, day) =>
    (d.days = d.days.includes(day) ? d.days.filter((x) => x !== day) : [...d.days, day]);
  const draftCups = (p) => (robot.meal_insert_size ? fmtCups(p * robot.meal_insert_size) : `${p}×`);

  async function saveSchedule() {
    if (!draft.length)
      return (editError = "Keep at least one meal — pause one instead of removing them all.");
    for (const d of draft) {
      if (!d.name.trim()) return (editError = "Every meal needs a name.");
      if (!/^\d{1,2}:\d{2}/.test(d.time)) return (editError = "Every meal needs a time.");
      if (!d.days.length) return (editError = "Every meal needs at least one day.");
    }
    const mealsPayload = draft.map((d) => {
      const [hour, minute] = d.time.split(":").map(Number);
      return { meal_number: d.meal_number, name: d.name.trim(), hour, minute, portions: Number(d.portions), days: d.days, paused: d.paused };
    });
    saving = true;
    editError = null;
    const res = await run("schedule", { meals: mealsPayload });
    saving = false;
    if (res && res.ok) editing = false;
    else editError = "The feeder didn't accept the schedule — it may be offline. Try again.";
  }

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
        <span class="schedule-head-label">
          {robot.schedule?.name ? `Feeding schedule · ${robot.schedule.name}` : "Feeding schedule"}
        </span>
        <button class="btn sm secondary" disabled={busy} onclick={openEditor}>Edit</button>
      </div>
      <table class="schedule-table">
        <tbody>
          {#each meals as m}
            <tr class:dim={m.paused} class:done={isDispensed(m)}>
              <td class="schedule-time">{fmtMealTime(m.hour, m.minute)}</td>
              <td class="schedule-name">{m.name}</td>
              <td class="schedule-days">{m.every_day ? "Every day" : m.days.join(" ")}</td>
              <td class="schedule-portion">{m.cups != null ? fmtCups(m.cups) : `${m.portions}×`}</td>
              <td class="schedule-action">
                {#if m.paused}
                  <span class="schedule-paused">Paused</span>
                {:else if m.meal_number != null && !isDispensed(m)}
                  <button class="chip" class:active={upcomingSkip(m.skip)} disabled={busy}
                    onclick={() => run("skip-meal", { meal_number: m.meal_number, skip: !upcomingSkip(m.skip) })}>
                    {upcomingSkip(m.skip) ? `Skip ${fmtSkip(m.skip)} ✕` : "Skip next"}
                  </button>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
      <div class="schedule-note">Skip drops just the next one; pausing and other edits live in Edit.</div>
    </div>
  {/if}

  {#if error}<div class="robot-error">{error}</div>{/if}
</div>

{#if editing}
  <Modal title="Edit feeding schedule" onClose={() => (editing = false)}>
    <div class="sched-edit">
      {#each draft as d (d.key)}
        <div class="sched-edit-meal">
          <div class="sched-edit-row">
            <input class="sched-edit-name" type="text" bind:value={d.name} placeholder="Meal name" maxlength="64" />
            <input class="sched-edit-time" type="time" bind:value={d.time} />
            <span class="sched-edit-portions">
              <button type="button" aria-label="Fewer portions" disabled={d.portions <= 1}
                onclick={() => (d.portions = Math.max(1, d.portions - 1))}>−</button>
              <b>{d.portions}×</b>
              <button type="button" aria-label="More portions" disabled={d.portions >= 16}
                onclick={() => (d.portions = Math.min(16, d.portions + 1))}>+</button>
              <span class="sched-edit-cups">≈ {draftCups(d.portions)}</span>
            </span>
            <button class="sched-edit-remove" title="Remove meal" aria-label="Remove meal"
              onclick={() => removeMeal(d.key)}>✕</button>
          </div>
          <div class="sched-edit-days">
            {#each DAYS as day}
              <button type="button" class="chip" class:active={d.days.includes(day)}
                onclick={() => toggleDay(d, day)}>{day}</button>
            {/each}
            <button type="button" class="chip sched-edit-pause" class:active={d.paused}
              onclick={() => (d.paused = !d.paused)}>{d.paused ? "Paused" : "Pause"}</button>
          </div>
        </div>
      {/each}

      <button class="btn sm secondary sched-edit-add" onclick={addMeal}>+ Add meal</button>

      {#if editError}<div class="sched-edit-error">{editError}</div>{/if}

      <div class="sched-edit-actions">
        <button class="btn secondary" disabled={saving} onclick={() => (editing = false)}>Cancel</button>
        <button class="btn" disabled={saving} onclick={saveSchedule}>{saving ? "Saving…" : "Save schedule"}</button>
      </div>
    </div>
  </Modal>
{/if}
