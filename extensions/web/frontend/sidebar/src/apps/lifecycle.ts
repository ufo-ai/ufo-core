type LifecycleStateName = "booting" | "active" | "idle" | "unmounted";
type LifecycleWorkKind =
  | "startup"
  | "observation"
  | "unary"
  | "stream"
  | "timeout"
  | "interval";

export type ApplicationLifecycleSnapshot = {
  version: 1;
  generation: number;
  epoch: number;
  mounted: boolean;
  state: LifecycleStateName;
  revision: number;
  blockingWork: number;
  blocking: Record<LifecycleWorkKind, number>;
};

type Lease = {
  generation: number;
  kind: LifecycleWorkKind;
};

type TimerRecord = {
  lease: number;
  releaseTimer?: number;
  cancelRelease?: () => void;
  stop?: () => void;
};

type LifecycleState = {
  generation: number;
  epoch: number;
  mounted: boolean;
  unmounted: boolean;
  revision: number;
  nextLease: number;
  leases: Map<number, Lease>;
  root: HTMLElement | null;
  observer: MutationObserver | null;
  startupLease: number | null;
  startupFinish: number;
  callbackGeneration: number | null;
  timers: Map<number, TimerRecord>;
  nativeSetTimeout: typeof window.setTimeout;
  nativeClearTimeout: typeof window.clearTimeout;
  nativeSetInterval: typeof window.setInterval;
  nativeClearInterval: typeof window.clearInterval;
  nativeRequestAnimationFrame: typeof window.requestAnimationFrame;
  nativeCancelAnimationFrame: typeof window.cancelAnimationFrame;
  timersInstalled: boolean;
  controller: ApplicationLifecycleController;
};

type ApplicationLifecycleController = {
  snapshot(): ApplicationLifecycleSnapshot;
  afterPaint(): Promise<void>;
  beginObservation(): number;
  endObservation(epoch: number): Promise<void>;
};

const PUBLIC_PROPERTY = "__ufoApplicationLifecycle";
const LIFECYCLE_ATTRIBUTES = new Set([
  "data-ufo-application-mounted",
  "data-ufo-application-state",
  "data-ufo-application-generation",
  "data-ufo-application-epoch",
  "data-ufo-application-revision",
  "data-ufo-application-blocking-work",
]);

if (Object.getOwnPropertyDescriptor(window, PUBLIC_PROPERTY)) {
  throw new Error("application lifecycle is already installed");
}

function currentState(): LifecycleStateName {
  if (state.unmounted) return "unmounted";
  if (!state.mounted) return "booting";
  return state.leases.size ? "active" : "idle";
}

function counts(): Record<LifecycleWorkKind, number> {
  const result: Record<LifecycleWorkKind, number> = {
    startup: 0,
    observation: 0,
    unary: 0,
    stream: 0,
    timeout: 0,
    interval: 0,
  };
  for (const lease of state.leases.values()) {
    if (lease.generation === state.generation) result[lease.kind] += 1;
  }
  return Object.freeze(result);
}

function snapshot(): ApplicationLifecycleSnapshot {
  return Object.freeze({
    version: 1,
    generation: state.generation,
    epoch: state.epoch,
    mounted: state.mounted,
    state: currentState(),
    revision: state.revision,
    blockingWork: state.leases.size,
    blocking: counts(),
  });
}

function publish(): void {
  if (!state.root) return;
  const value = snapshot();
  state.root.dataset.ufoApplicationMounted = String(value.mounted);
  state.root.dataset.ufoApplicationState = value.state;
  state.root.dataset.ufoApplicationGeneration = String(value.generation);
  state.root.dataset.ufoApplicationEpoch = String(value.epoch);
  state.root.dataset.ufoApplicationRevision = String(value.revision);
  state.root.dataset.ufoApplicationBlockingWork = String(value.blockingWork);
}

function revise(): void {
  state.revision += 1;
  publish();
}

function acquire(kind: LifecycleWorkKind): number {
  if (
    state.unmounted ||
    (state.callbackGeneration !== null && state.callbackGeneration !== state.generation)
  )
    return 0;
  const lease = ++state.nextLease;
  state.leases.set(lease, { generation: state.generation, kind });
  revise();
  return lease;
}

function release(lease: number): void {
  const heldLease = state.leases.get(lease);
  if (!heldLease) return;
  state.leases.delete(lease);
  if (heldLease.generation === state.generation) revise();
}

function scheduleAfterPaint(work: () => void): () => void {
  let first = 0;
  let second = 0;
  first = state.nativeRequestAnimationFrame(() => {
    second = state.nativeRequestAnimationFrame(work);
  });
  return () => {
    state.nativeCancelAnimationFrame(first);
    state.nativeCancelAnimationFrame(second);
  };
}

function afterPaint(): Promise<void> {
  return new Promise((resolve) => scheduleAfterPaint(resolve));
}

function releaseAfterPaint(lease: number): void {
  scheduleAfterPaint(() => release(lease));
}

function beginObservation(): number {
  if (!state.mounted || state.unmounted) throw new Error("application is not mounted");
  if ([...state.leases.values()].some((lease) => lease.kind === "observation")) {
    throw new Error("application observation epoch is already active");
  }
  state.epoch += 1;
  acquire("observation");
  return state.epoch;
}

function endObservation(epoch: number): Promise<void> {
  const lease = [...state.leases.entries()].find(
    ([, candidate]) => candidate.kind === "observation" && candidate.generation === state.generation,
  )?.[0];
  if (epoch !== state.epoch || lease === undefined) {
    return Promise.reject(new Error("application observation epoch is not active"));
  }
  return new Promise((resolve) => {
    scheduleAfterPaint(() => {
      release(lease);
      resolve();
    });
  });
}

function createState(): LifecycleState {
  const controller: ApplicationLifecycleController = Object.freeze({
    snapshot,
    afterPaint,
    beginObservation,
    endObservation,
  });
  return {
    generation: 1,
    epoch: 0,
    mounted: false,
    unmounted: false,
    revision: 0,
    nextLease: 0,
    leases: new Map(),
    root: null,
    observer: null,
    startupLease: null,
    startupFinish: 0,
    callbackGeneration: null,
    timers: new Map(),
    nativeSetTimeout: window.setTimeout.bind(window),
    nativeClearTimeout: window.clearTimeout.bind(window),
    nativeSetInterval: window.setInterval.bind(window),
    nativeClearInterval: window.clearInterval.bind(window),
    nativeRequestAnimationFrame: window.requestAnimationFrame.bind(window),
    nativeCancelAnimationFrame: window.cancelAnimationFrame.bind(window),
    timersInstalled: false,
    controller,
  };
}

const state = createState();

if (!state.startupLease && !state.mounted && !state.unmounted) {
  state.startupLease = acquire("startup");
}

Object.defineProperty(window, PUBLIC_PROPERTY, {
  configurable: false,
  enumerable: false,
  get: () => state.controller,
});

function cancelTrackedTimers(): void {
  for (const id of [...state.timers.keys()]) {
    state.nativeClearTimeout(id);
    cancelTrackedTimer(id);
  }
}

function cancelTrackedTimer(id: number): void {
  const timer = state.timers.get(id);
  if (!timer) return;
  if (timer.stop) {
    timer.stop();
    return;
  }
  state.timers.delete(id);
  if (timer.releaseTimer !== undefined) state.nativeClearTimeout(timer.releaseTimer);
  timer.cancelRelease?.();
  release(timer.lease);
}

export function beginApplicationMount(root: HTMLElement): number {
  if (state.root && !state.unmounted) throw new Error("application is already mounted");
  if (state.unmounted) {
    if (state.leases.size || state.callbackGeneration !== null) {
      throw new Error("application work is still settling");
    }
    state.observer?.disconnect();
    cancelTrackedTimers();
    state.generation += 1;
    state.epoch = 0;
    state.mounted = false;
    state.unmounted = false;
    state.leases.clear();
    state.startupLease = acquire("startup");
  }
  state.root = root;
  state.observer?.disconnect();
  state.observer = new MutationObserver((records) => {
    if (
      records.some(
        (record) => record.type !== "attributes" || !LIFECYCLE_ATTRIBUTES.has(record.attributeName ?? ""),
      )
    ) {
      revise();
    }
  });
  state.observer.observe(root, {
    attributes: true,
    characterData: true,
    childList: true,
    subtree: true,
  });
  revise();
  return state.generation;
}

export function markApplicationMounted(generation: number): void {
  if (generation !== state.generation || state.unmounted) return;
  state.mounted = true;
  revise();
}

export function finishApplicationStartup(generation: number): () => void {
  const finish = ++state.startupFinish;
  const cancel = scheduleAfterPaint(() => {
    if (generation !== state.generation || finish !== state.startupFinish) return;
    const lease = state.startupLease;
    state.startupLease = null;
    if (lease !== null) release(lease);
  });
  return () => {
    state.startupFinish += 1;
    cancel();
  };
}

export function unmountApplication(generation: number): void {
  if (generation !== state.generation) return;
  state.startupFinish += 1;
  state.observer?.disconnect();
  state.observer = null;
  state.mounted = false;
  state.unmounted = true;
  cancelTrackedTimers();
  const startup = state.startupLease;
  state.startupLease = null;
  if (startup !== null) release(startup);
  for (const [lease, work] of state.leases) {
    if (work.kind === "observation") release(lease);
  }
  state.revision += 1;
  publish();
  state.root = null;
}

export function beginLifecycleWork(kind: "unary" | "stream"): number {
  return acquire(kind);
}

export function reviseLifecycleWork(lease: number): void {
  const work = state.leases.get(lease);
  if (work?.generation === state.generation) revise();
}

export function settleLifecycleWork(lease: number): void {
  releaseAfterPaint(lease);
}

export function cancelLifecycleWork(lease: number): void {
  release(lease);
}

export function setNativeTimeout(callback: () => void, delay: number): number {
  return state.nativeSetTimeout(callback, delay);
}

function installTimers(): void {
  if (state.timersInstalled) return;
  state.timersInstalled = true;
  window.setTimeout = ((handler: TimerHandler, timeout?: number, ...args: unknown[]): number => {
    const lease = acquire("timeout");
    const generation = state.generation;
    if (typeof handler === "string") {
      const id = state.nativeSetTimeout(handler, timeout, ...args);
      const timer: TimerRecord = { lease };
      timer.releaseTimer = state.nativeSetTimeout(() => state.timers.delete(id), timeout);
      timer.cancelRelease = scheduleAfterPaint(() => {
        const settling = state.timers.get(id);
        if (settling !== timer) return;
        settling.lease = 0;
        settling.cancelRelease = undefined;
        release(lease);
      });
      state.timers.set(id, timer);
      return id;
    }
    let id = 0;
    id = state.nativeSetTimeout(function (this: Window, ...callbackArgs: unknown[]) {
      const timer = state.timers.get(id);
      state.timers.delete(id);
      timer?.cancelRelease?.();
      const callbackLease = timer?.lease || acquire("timeout");
      const previous = state.callbackGeneration;
      state.callbackGeneration = generation;
      if (generation === state.generation) revise();
      try {
        const result = handler.apply(this, callbackArgs);
        Promise.resolve(result).finally(() => releaseAfterPaint(callbackLease));
      } catch (error) {
        releaseAfterPaint(callbackLease);
        throw error;
      } finally {
        state.callbackGeneration = previous;
      }
    }, timeout, ...args);
    const timer: TimerRecord = { lease };
    timer.cancelRelease = scheduleAfterPaint(() => {
      const settling = state.timers.get(id);
      if (settling !== timer) return;
      settling.lease = 0;
      settling.cancelRelease = undefined;
      release(lease);
    });
    state.timers.set(id, timer);
    return id;
  }) as typeof window.setTimeout;
  window.clearTimeout = ((id?: number): void => {
    if (id !== undefined) cancelTrackedTimer(id);
    state.nativeClearTimeout(id);
  }) as typeof window.clearTimeout;
  window.setInterval = ((handler: TimerHandler, timeout?: number, ...args: unknown[]): number => {
    const generation = state.generation;
    let id = 0;
    let running = 0;
    let active = true;
    const hold = (): void => {
      running += 1;
      const timer = state.timers.get(id);
      if (!timer) return;
      timer.cancelRelease?.();
      timer.cancelRelease = undefined;
      if (!timer.lease) timer.lease = acquire("interval");
    };
    const settle = (): void => {
      running -= 1;
      const timer = state.timers.get(id);
      if (running > 0 || !timer || !timer.lease) return;
      const held = timer.lease;
      timer.cancelRelease = scheduleAfterPaint(() => {
        const settling = state.timers.get(id);
        if (settling && settling.lease === held) {
          settling.lease = 0;
          settling.cancelRelease = undefined;
          if (!active) state.timers.delete(id);
        }
        release(held);
      });
    };
    const stop = (): void => {
      active = false;
      const timer = state.timers.get(id);
      if (!timer) return;
      timer.cancelRelease?.();
      timer.cancelRelease = undefined;
      if (running > 0) return;
      state.timers.delete(id);
      cancelLifecycleWork(timer.lease);
    };
    const wrapped =
      typeof handler === "string"
        ? handler
        : function (this: Window, ...callbackArgs: unknown[]) {
            const previous = state.callbackGeneration;
            state.callbackGeneration = generation;
            hold();
            try {
              const result = handler.apply(this, callbackArgs);
              Promise.resolve(result).finally(settle);
              return result;
            } catch (error) {
              settle();
              throw error;
            } finally {
              state.callbackGeneration = previous;
            }
          };
    id = state.nativeSetInterval(wrapped, timeout, ...args);
    state.timers.set(id, { lease: 0, stop });
    return id;
  }) as typeof window.setInterval;
  window.clearInterval = ((id?: number): void => {
    if (id !== undefined) cancelTrackedTimer(id);
    state.nativeClearInterval(id);
  }) as typeof window.clearInterval;
}

installTimers();
