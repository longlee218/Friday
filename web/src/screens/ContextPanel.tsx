import { useEffect, useState } from "react";

import { api } from "../api";
import type { ChannelContext } from "../api-types";
import { useAsync } from "../useAsync";
import { Pill } from "../ui";

/** One room's context, for the dialog the room opens.
 *
 *  It was a screen with its own channel picker, on its own tab — so editing
 *  what a room is told meant leaving the room, finding it again in a second
 *  list, and matching it by id. The room already knows which channel it is;
 *  the picker was asking a question the caller had already answered.
 *
 *  Everything below is unchanged from that screen and still true: pairs
 *  rather than YAML so a malformed file is unreachable, a save that does
 *  nothing until reloaded, and the rendered prompt beside the values so
 *  what the model actually reads is visible. */
export function ContextPanel({ channelId }: { channelId: string }) {
  const exists = useAsync(() => api.context(channelId), [channelId]);
  const [problem, setProblem] = useState<string | null>(null);

  if (exists.error) {
    return <p className="mono error">{exists.error}</p>;
  }
  if (!exists.value) return <p className="empty">Loading…</p>;

  if (!exists.value.exists) {
    return (
      <>
        <p className="faint">
          This room has no context file. Creating one gives it a place for what
          is true here — an escalation path, a name for the system they mean,
          anything a reply should assume. The machine never writes this part.
        </p>
        {problem && <p className="mono error">{problem}</p>}
        <div>
          <button
            className="primary"
            onClick={async () => {
              setProblem(null);
              try {
                await api.createContext(channelId);
                exists.reload();
              } catch (e) {
                setProblem((e as Error).message);
              }
            }}
          >
            Create it
          </button>
        </div>
      </>
    );
  }

  return <Editor channelId={channelId} />;
}


function Editor({ channelId }: { channelId: string }) {
  const loaded = useAsync(() => api.context(channelId), [channelId]);
  const [pairs, setPairs] = useState<[string, string][]>([]);
  const [dirty, setDirty] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    if (loaded.value) {
      setPairs(Object.entries(loaded.value.overrides));
      setDirty(false);
    }
  }, [loaded.value]);

  if (loaded.error) {
    return <div className="card error">Could not load {channelId}: {loaded.error}</div>;
  }
  if (!loaded.value) return <div className="empty">Loading…</div>;
  const it: ChannelContext = loaded.value;

  const edit = (i: number, which: 0 | 1, value: string) => {
    setPairs((was) =>
      was.map((pair, at) =>
        at === i ? ((which === 0 ? [value, pair[1]] : [pair[0], value]) as [string, string]) : pair,
      ),
    );
    setDirty(true);
  };

  // `Object.fromEntries` keeps the last of two rows sharing a key, so the
  // first would vanish on the refetch with nothing said — on the screen whose
  // whole job is precision about what the model is told. D9 chose pairs over
  // a YAML field to make a bad state unreachable; silently losing a line is a
  // different bad state, so saving is refused until it is resolved.
  const named = pairs.filter(([k]) => k).map(([k]) => k);
  const duplicated = named.filter((k, i) => named.indexOf(k) !== i);

  async function save() {
    setProblem(null);
    if (duplicated.length) {
      setProblem(`two lines share a key: ${[...new Set(duplicated)].join(", ")}`);
      return;
    }
    try {
      await api.setOverrides(channelId, Object.fromEntries(pairs.filter(([k]) => k)));
      setDirty(false);
      loaded.reload();
    } catch (e) {
      setProblem((e as Error).message);
    }
  }

  async function reload() {
    // The only handler here that was unguarded, and the worst one to leave
    // that way: D8 makes this button the single thing that turns a saved edit
    // into a live one, so a failed POST that reads as "nothing happened" is
    // exactly the wrong silence.
    setProblem(null);
    try {
      const answer = await api.reload();
      if (answer.problems.length) setProblem(answer.problems.join("\n"));
    } catch (e) {
      setProblem((e as Error).message);
    }
    loaded.reload();
  }

  return (
    <>
      <section className="card">
        <div className="row wrap" style={{ justifyContent: "space-between" }}>
          <div className="row wrap">
            <h2>overrides · {channelId}</h2>
            <span className="faint">yours; the machine never writes here</span>
          </div>
          <div className="row">
            <button
              className="primary"
              disabled={!dirty || duplicated.length > 0}
              onClick={save}
            >
              Save
            </button>
            {/* One rule for when an edit takes effect (D8): this button, and
                it re-reads every file, so a hand-edit lands the same way. */}
            <button onClick={reload}>Reload into the agents</button>
          </div>
        </div>

        {/* Derived from what the server says, not from a local flag. A flag
            set on save is wiped by the refetch that follows it — the notice
            flashed and vanished, which a browser found and the type checker
            could not. This also catches a file edited by hand, and survives a
            page refresh, because "on disk" and "in the agents" are two facts
            the server already reports. */}
        {it.prompt !== (it.live ?? "") && (
          <p className="mono warn">
            What is on disk is not what the running agents are using. Press
            “Reload into the agents” to make it live.
          </p>
        )}
        {problem && <p className="mono error">{problem}</p>}
        {it.also_in.length > 0 && (
          <p className="mono">
            <Pill tone="warn" label="also set elsewhere" />{" "}
            {it.also_in.join(", ")} — the model is shown both sections and
            reconciles them itself; there is no merge that picks a winner.
          </p>
        )}

        <table>
          <thead>
            <tr>
              <th style={{ width: "30%" }}>key</th>
              <th>value</th>
              <th style={{ width: 40 }} />
            </tr>
          </thead>
          <tbody>
            {pairs.map(([key, value], i) => (
              <tr key={i}>
                <td>
                  <input
                    type="text"
                    aria-label={`Key ${i + 1}`}
                    value={key}
                    onChange={(e) => edit(i, 0, e.target.value)}
                  />
                  {key && duplicated.includes(key) && (
                    <span className="pill bad">duplicate key</span>
                  )}
                </td>
                <td>
                  <input
                    type="text"
                    aria-label={`Value for ${key || `key ${i + 1}`}`}
                    value={value}
                    onChange={(e) => edit(i, 1, e.target.value)}
                  />
                </td>
                <td>
                  <button
                    aria-label={`Remove ${key || `key ${i + 1}`}`}
                    onClick={() => {
                      setPairs((was) => was.filter((_, at) => at !== i));
                      setDirty(true);
                    }}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <button
          style={{ marginTop: 8 }}
          onClick={() => {
            setPairs((was) => [...was, ["", ""]]);
            setDirty(true);
          }}
        >
          Add a line
        </button>
      </section>

      <section className="card">
        <h2>What the model is told about this room</h2>
        <p className="faint">
          The three layers as an agent actually receives them — escaped, in
          order. This is the rendering on disk; “live” below is what the running
          agents hold.
        </p>
        <pre>{it.prompt || "nothing"}</pre>
        <details>
          <summary>Live (what the agents are using now)</summary>
          <pre>{it.live || "nothing"}</pre>
        </details>
        <details>
          <summary>Machine-written (derived)</summary>
          <pre>{JSON.stringify(it.derived, null, 2)}</pre>
        </details>
        <details>
          <summary>Everywhere (base.yaml)</summary>
          <pre>{JSON.stringify(it.base, null, 2)}</pre>
        </details>
      </section>
    </>
  );
}
