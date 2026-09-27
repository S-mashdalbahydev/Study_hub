"use client";
import { useEffect, useState } from "react";
import { Check, Cloud, Download, HardDrive, Plug, Trash2 } from "lucide-react";
import { AccountSettings } from "./account-settings";
import {
  api,
  type ConnectionDraft,
  type PullProgress,
  type RecommendedModel,
  type TutorSettings,
} from "@/lib/api";
import { Button } from "./ui/button";
import { Confirm } from "./ui/confirm";
import { AppHeader } from "./app-header";
import { AppearanceControl } from "./theme";

const EMPTY: ConnectionDraft = {
  label: "",
  kind: "openai",
  base_url: "",
  api_key: "",
  model: "",
};

function Recommended({
  model,
  onDone,
}: {
  model: RecommendedModel;
  onDone: () => void;
}) {
  const [progress, setProgress] = useState<PullProgress | null>(null);
  const [error, setError] = useState("");
  const busy = progress !== null && !progress.done && !progress.error;
  return (
    <li className="tutor-model">
      <div className="tutor-model-main">
        <strong>{model.name}</strong>
        <span>Not installed · {model.good_for}</span>
        {busy && (
          <>
            <span className="meter" aria-hidden>
              <span style={{ width: `${progress?.percent ?? 0}%` }} />
            </span>
            <small role="status">
              {progress?.status}
              {progress?.percent != null ? ` · ${progress.percent}%` : ""}
            </small>
          </>
        )}
        {error && (
          <small className="error" role="alert">
            {error}
          </small>
        )}
      </div>
      <span className="tutor-size">{model.size_gb} GB</span>
      <Button
        size="sm"
        variant="outline"
        disabled={busy}
        aria-label={`Download ${model.name}`}
        onClick={async () => {
          setError("");
          setProgress({ status: "Starting…" });
          try {
            await api.pullModel(model.name, (part) => {
              setProgress(part);
              if (part.error) setError(part.error);
            });
            onDone();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setProgress(null);
          }
        }}
      >
        <Download size={14} aria-hidden /> {busy ? "Downloading…" : "Download"}
      </Button>
    </li>
  );
}

function AddConnection({ onSaved }: { onSaved: () => void }) {
  const [draft, setDraft] = useState<ConnectionDraft>(EMPTY);
  const [found, setFound] = useState<string[] | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  function field(key: keyof ConnectionDraft) {
    return {
      value: draft[key],
      onChange: (
        e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>,
      ) => {
        setDraft({ ...draft, [key]: e.target.value });
        setFound(null);
        setNote("");
      },
    };
  }
  return (
    <form
      className="tutor-form"
      aria-label="Add a connection"
      onSubmit={(e) => e.preventDefault()}
    >
      <div className="field-row">
        <label className="field">
          Type
          <select {...field("kind")}>
            <option value="openai">OpenAI-compatible API</option>
            <option value="ollama">Another Ollama server</option>
          </select>
        </label>
        <label className="field">
          Name
          <input placeholder="My provider" {...field("label")} />
        </label>
      </div>
      <label className="field">
        Address
        <input
          inputMode="url"
          placeholder={
            draft.kind === "ollama"
              ? "http://192.168.1.20:11434"
              : "https://api.example.com/v1"
          }
          {...field("base_url")}
        />
      </label>
      {draft.kind === "openai" && (
        <label className="field">
          API key
          <input
            type="password"
            autoComplete="off"
            placeholder="Stored on this machine and never shown again"
            {...field("api_key")}
          />
        </label>
      )}
      <label className="field">
        Model
        {found?.length ? (
          <select {...field("model")}>
            <option value="">Choose a model…</option>
            {found.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        ) : (
          <input
            placeholder="Test the connection to list its models"
            {...field("model")}
          />
        )}
      </label>
      {note && (
        <p className="tutor-note" role="status">
          {note}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="dialog-actions start">
        <Button
          type="button"
          variant="outline"
          disabled={busy || !draft.base_url.trim()}
          onClick={async () => {
            setBusy(true);
            setError("");
            setNote("");
            try {
              const result = await api.testTutor(draft);
              setFound(result.models);
              setNote(
                `Connected in ${result.latency_ms} ms · ${result.models.length} models available`,
              );
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <Plug size={14} aria-hidden /> Test connection
        </Button>
        <Button
          type="button"
          disabled={busy || !draft.base_url.trim()}
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              await api.saveTutor(draft);
              setDraft(EMPTY);
              setFound(null);
              setNote("");
              onSaved();
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          Save connection
        </Button>
      </div>
    </form>
  );
}

const SECTIONS = [
  ["models", "Tutor models"],
  ["connections", "Connections"],
  ["appearance", "Appearance"],
  ["account", "Account"],
] as const;

export function TutorSettingsView() {
  const [data, setData] = useState<TutorSettings | null>(null);
  const [error, setError] = useState("");
  async function reload() {
    try {
      setData(await api.tutors());
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    reload();
  }, []);
  async function run(action: () => Promise<unknown>) {
    try {
      await action();
      await reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const local =
    data?.catalog.models.filter((m) => m.provider === "ollama") ?? [];
  const installed = new Set(local.map((m) => m.model.replace(/:latest$/, "")));
  const selected = data?.catalog.selected;
  return (
    <div className="app">
      <AppHeader />
      <main id="main" className="shell">
        <section className="page-intro">
          <h1>Settings</h1>
          <p>
            Choose which model tutors you and how StudyHub looks. Your sources
            are always parsed and indexed on this machine.
          </p>
        </section>
        {error && (
          <p className="notice error" role="alert">
            {error}
          </p>
        )}
        <div className="settings-layout">
          <nav className="settings-nav" aria-label="Settings sections">
            {SECTIONS.map(([id, label]) => (
              <a key={id} href={`#${id}`}>
                {label}
              </a>
            ))}
          </nav>
          <div>
            <section
              id="models"
              className="settings-section"
              aria-labelledby="models-heading"
            >
              <h2 id="models-heading">Tutor models</h2>
              <p>
                A <strong>local model</strong> runs on this machine: private and
                free, limited by your hardware. An{" "}
                <strong>API connection</strong> sends your question and the
                cited passages to that provider. Reading sessions can still pick
                a different model from the Tutor menu.
              </p>
              {!data ? (
                <p className="notice" role="status">
                  Loading tutor models…
                </p>
              ) : (
                <>
                  <p className="tutor-current" role="status">
                    <span className="hint">Default tutor</span>
                    <strong>{selected?.name ?? "None available yet"}</strong>
                    {selected && (
                      <span className="hint">
                        {selected.provider === "ollama"
                          ? "Local model"
                          : "API connection"}
                      </span>
                    )}
                    {data.catalog.error && (
                      <span className="error">{data.catalog.error}</span>
                    )}
                  </p>
                  <h3>
                    <HardDrive size={16} aria-hidden /> On this machine
                  </h3>
                  <ul className="tutor-list">
                    {local.map((model) => (
                      <li className="tutor-model" key={model.id}>
                        <div className="tutor-model-main">
                          <strong>{model.model}</strong>
                          <span>Installed · runs locally</span>
                        </div>
                        <div className="tutor-model-actions">
                          {selected?.id === model.id ? (
                            <span className="tutor-installed">
                              <Check size={14} aria-hidden /> In use
                            </span>
                          ) : (
                            <Button
                              size="sm"
                              variant="outline"
                              aria-label={`Use ${model.model}`}
                              onClick={() =>
                                run(() => api.activateModel(model.model))
                              }
                            >
                              Use this
                            </Button>
                          )}
                          {data.can_manage_models && (
                          <Confirm
                            trigger={
                              <Button
                                size="icon"
                                variant="ghost"
                                aria-label={`Delete ${model.model}`}
                                title={`Delete ${model.model}`}
                              >
                                <Trash2 size={15} aria-hidden />
                              </Button>
                            }
                            title={`Delete ${model.model}?`}
                            description={`Its files are removed from this machine${selected?.id === model.id ? " and the default tutor switches to Auto" : ""}. Reading sessions that chose it will ask you to pick another model. You can download it again later.`}
                            confirmLabel="Delete model"
                            onConfirm={async () => {
                              await api.deleteModel(model.model);
                              await reload();
                            }}
                          />
                          )}
                        </div>
                      </li>
                    ))}
                    {data.can_manage_models && data.recommended
                      .filter(
                        (m) => !installed.has(m.name.replace(/:latest$/, "")),
                      )
                      .map((model) => (
                        <Recommended
                          key={model.name}
                          model={model}
                          onDone={reload}
                        />
                      ))}
                    {!local.length && !data.recommended.length && (
                      <li className="tutor-empty">
                        No local models found. Is Ollama running?
                      </li>
                    )}
                  </ul>
                </>
              )}
            </section>
            <section
              id="connections"
              className="settings-section"
              aria-labelledby="connections-heading"
            >
              <h2 id="connections-heading">Connections</h2>
              <p>
                Connect an OpenAI-compatible API or another Ollama server. Keys
                stay on this machine.
              </p>
              {data && (
                <>
                  <h3>
                    <Cloud size={16} aria-hidden /> Your connections
                  </h3>
                  <ul className="tutor-list">
                    {data.providers.map((provider) => (
                      <li className="tutor-model" key={provider.id}>
                        <div className="tutor-model-main">
                          <strong>{provider.label}</strong>
                          <span>
                            {provider.kind === "openai" ? "API" : "Ollama"} ·{" "}
                            {provider.base_url}
                            {provider.model ? ` · ${provider.model}` : ""}
                            {provider.key_present ? " · key saved" : ""}
                          </span>
                        </div>
                        <div className="tutor-model-actions">
                          {provider.active ? (
                            <span className="tutor-installed">
                              <Check size={14} aria-hidden /> In use
                            </span>
                          ) : (
                            <Button
                              size="sm"
                              variant="outline"
                              aria-label={`Use ${provider.label}`}
                              onClick={() =>
                                run(() => api.activateTutor(provider.id))
                              }
                            >
                              Use this
                            </Button>
                          )}
                          <Confirm
                            trigger={
                              <Button
                                size="icon"
                                variant="ghost"
                                aria-label={`Delete ${provider.label}`}
                                title={`Delete ${provider.label}`}
                              >
                                <Trash2 size={15} aria-hidden />
                              </Button>
                            }
                            title={`Delete “${provider.label}”?`}
                            description={`The connection to ${provider.base_url}${provider.key_present ? " and its saved API key are" : " is"} removed from this machine${provider.active ? ", and the default tutor switches to Auto" : ""}. Reading sessions that chose it will ask you to pick another model.`}
                            confirmLabel="Delete connection"
                            onConfirm={async () => {
                              await api.deleteTutor(provider.id);
                              await reload();
                            }}
                          />
                        </div>
                      </li>
                    ))}
                    {!data.providers.length && (
                      <li className="tutor-empty">No connections yet.</li>
                    )}
                  </ul>
                  <h3>
                    <Plug size={16} aria-hidden /> Add a connection
                  </h3>
                  <AddConnection onSaved={reload} />
                </>
              )}
            </section>
            <section
              id="appearance"
              className="settings-section"
              aria-labelledby="appearance-heading"
            >
              <h2 id="appearance-heading">Appearance</h2>
              <p>
                System follows your device. Original PDF pages stay white in
                every theme.
              </p>
              <AppearanceControl />
            </section>
            <AccountSettings />
          </div>
        </div>
      </main>
    </div>
  );
}
