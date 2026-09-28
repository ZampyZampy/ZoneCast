// Periodic refresh that only runs while it's useful: never in a
// background tab, and only while `when()` holds (e.g. its tab is open).
// Coming back to the page refreshes immediately instead of waiting a
// full interval.
const pollers = new Set();

export function poller(fn, intervalMs, { when = () => true } = {}) {
    let timer = null;
    let running = false;
    const tick = async () => {
        if (running || document.hidden || !when()) return;
        running = true;
        try { await fn(); } catch (err) { /* the callee reports its own errors */ } finally { running = false; }
    };
    const p = {
        start() { if (!timer) timer = setInterval(tick, intervalMs); },
        stop() { clearInterval(timer); timer = null; },
        setInterval(ms) { intervalMs = ms; if (timer) { this.stop(); this.start(); } },
        now: tick,
    };
    pollers.add(p);
    return p;
}

document.addEventListener('visibilitychange', () => {
    if (!document.hidden) pollers.forEach(p => p.now());
});
