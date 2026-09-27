export type Document = {
  id: string;
  title: string;
  filename: string;
  kind: string;
  status: string;
  error: string | null;
  created_at: string;
  source_type: "uploaded" | "research_book" | "learning_companion";
};
export type Citation = {
  id: number;
  label: string;
  page: number | null;
  document_id: string;
  url: string;
};
export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  status: "generating" | "complete" | "cancelled" | "failed";
};
export type Session = {
  id: string;
  document_id: string;
  page: number;
  model: string;
  mode: "guided" | "socratic" | "deep-dive" | "practical";
  layout: "reading" | "chat" | "split" | "focus";
  zoom: number;
  sidebar: boolean;
  chat_width: number;
  updated_at: string;
  messages?: Message[];
};
export type ReaderInfo = {
  page_count: number | null;
  cover: boolean;
  outline: {
    id?: string;
    title: string;
    page: number;
    level: number;
    parent_id?: string | null;
    anchor?: string;
  }[];
  figures: {
    id: string;
    label: string;
    caption: string;
    page: number;
    section_id?: string | null;
  }[];
  content?: string | null;
  chunks: { text: string; chunk_index: number; page: number | null }[];
  book?: Book | null;
};
export type LearningBlock = {
  id: string;
  type: string;
  title: string;
  body?: string;
  prompt?: string;
  response_type?: string;
  agreement?: string;
  differences?: string;
  citations?: SourceCitation[];
  sources?: { source: string; explanation: string; citation: SourceCitation }[];
};
export type SourceCitation = {
  source_id: string;
  title: string;
  locator: string;
  page?: number | null;
  timestamp_start?: string | null;
  url?: string | null;
};
export type ThemeChapter = {
  theme_id: string;
  document_id: string;
  chapter_id: string;
  title: string;
  page: number;
  end_page: number | null;
  done: boolean;
  book: string;
  kind: string;
};
export type LessonCitation = {
  n: number;
  source_id: string;
  title: string;
  page: number | null;
  url: string;
};
export type LearningTheme = {
  id: string;
  title: string;
  summary: string;
  position: number;
  lesson: string;
  exercise: string;
  citations: LessonCitation[];
  chapters: ThemeChapter[];
};
export type LearningSession = {
  id: string;
  title: string;
  learning_goal: string;
  status: string;
  themes: LearningTheme[];
  notebook: LearningBlock[];
  materials: {
    document_id: string;
    title: string;
    filename: string;
    kind: string;
  }[];
  answers: { block_id: string; answer: string; feedback: string }[];
  created_at: string;
  updated_at: string;
};
export type BookBlock = {
  id: string;
  type: string;
  markdown: string;
  origin: "original" | "ai" | "external";
  level?: number;
  title?: string;
  semantic_type?: string;
};
export type Book = {
  id: string;
  title: string;
  subtitle?: string | null;
  authors: string[];
  filename: string;
  cover: boolean;
  estimated_minutes: number;
  chapter_count: number;
  page_count: number;
  toc: {
    id: string;
    title: string;
    kind: string;
    level: number;
    parent_id?: string | null;
    reader_page: number;
    source_start_page?: number | null;
    source_end_page?: number | null;
  }[];
  pages: { id: string; number: number; blocks: BookBlock[] }[];
};
export type Tutor = {
  id: string;
  name: string;
  provider: string;
  model: string;
};
export type Catalog = {
  models: Tutor[];
  available: boolean;
  selected: Tutor | null;
  error: string | null;
  warnings: string[];
};
export type TutorProvider = {
  id: string;
  label: string;
  kind: "ollama" | "openai";
  base_url: string;
  model: string;
  key_present: boolean;
  active: boolean;
};
export type RecommendedModel = {
  name: string;
  size_gb: number;
  good_for: string;
};
export type TutorSettings = {
  /** Downloading and deleting models changes the shared server: administrator only. */
  can_manage_models: boolean;
  providers: TutorProvider[];
  recommended: RecommendedModel[];
  catalog: Catalog;
  ollama_url: string;
};
export type ConnectionDraft = {
  label: string;
  kind: "ollama" | "openai";
  base_url: string;
  api_key: string;
  model: string;
};
export type ConnectionTest = {
  ok: boolean;
  latency_ms: number;
  models: string[];
};
export type PullProgress = {
  status?: string;
  percent?: number | null;
  completed?: number;
  total?: number;
  done?: boolean;
  error?: string;
};
export type StreamPart =
  | { type: "start"; message_id: string; user_id: string }
  | { type: "text"; text: string }
  | { type: "status" | "error"; text: string }
  | { type: "citations"; citations: Citation[] }
  | { type: "done"; status: Message["status"] };
const base = "/backend";
// Every state-changing request carries this header. Another site cannot add a custom
// header to a cross-site request without a CORS preflight, which the API never grants.
const CSRF = { "X-StudyHub-CSRF": "1" };
export type Account = {
  user: {
    id: string;
    display_name: string;
    email: string;
    is_instance_admin: boolean;
  };
  workspace: { id: string; name: string; kind: string; role: string } | null;
  workspaces: { id: string; name: string; kind: string; role: string }[];
  deletion_cancelled?: boolean;
};
const AUTH_PAGES = ["/login", "/signup"];
/** A request came back 401: the session ended. Send the reader to log in, then back. */
function signedOut() {
  if (typeof window === "undefined") return;
  const here = window.location.pathname + window.location.search;
  if (AUTH_PAGES.includes(window.location.pathname)) return;
  window.location.assign("/login?next=" + encodeURIComponent(here));
}
export type ResearchJob = {
  id: string;
  mode: string;
  material_type: "study_guide" | "learning_companion";
  source_document_id: string | null;
  query: string;
  urls: string[];
  depth: string;
  level: string;
  goal: string;
  status: string;
  progress: { message?: string; completed?: number; total?: number };
  outline: string[];
  draft?: string;
  error: string | null;
  document_id: string | null;
  sources: {
    id: string;
    title: string;
    url: string;
    publisher: string;
    retained: number;
  }[];
  resources: {
    id: string;
    chapter: string;
    title: string;
    url: string;
    resource_type: "video" | "playlist_en" | "playlist_ar" | "article_or_demo";
    publisher: string;
    snippet: string;
    validation_reason: string;
    selected: boolean;
    thumbnail: string;
    video_count: number | null;
  }[];
};
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(base + path, {
    ...options,
    headers: { "Content-Type": "application/json", ...CSRF, ...options?.headers },
  });
  if (response.status === 401 && !path.startsWith("/api/auth/")) signedOut();
  if (!response.ok) {
    let message = "We could not complete that request.";
    try {
      const data = await response.json();
      if (typeof data.detail === "string") message = data.detail;
    } catch {}
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}
export const api = {
  signup: (data: { display_name: string; email: string; password: string }) =>
    request<Account>("/api/auth/signup", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  login: (data: { email: string; password: string }) =>
    request<Account>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  me: () => request<Account>("/api/auth/me"),
  deleteAccount: (password: string) =>
    request<{ purge_after: string; grace_days: number }>("/api/auth/account", {
      method: "DELETE",
      body: JSON.stringify({ password }),
    }),
  learningSessions: () => request<LearningSession[]>("/api/learning-sessions"),
  learningSession: (id: string) =>
    request<LearningSession>(`/api/learning-sessions/${id}`),
  createLearningSession: (data: {
    title: string;
    learning_goal: string;
    document_ids: string[];
  }) =>
    request<LearningSession>("/api/learning-sessions", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  generateNotebook: (id: string, themes: string[] = []) =>
    request<LearningSession>(`/api/learning-sessions/${id}/generate`, {
      method: "POST",
      body: JSON.stringify({ themes }),
    }),
  teachTheme: (sessionId: string, themeId: string) =>
    request<LearningSession>(
      `/api/learning-sessions/${sessionId}/themes/${themeId}/lesson`,
      { method: "POST" },
    ),
  answerTheme: (sessionId: string, themeId: string, answer: string) =>
    request<{ feedback: string }>(
      `/api/learning-sessions/${sessionId}/themes/${themeId}/answer`,
      { method: "POST", body: JSON.stringify({ answer }) },
    ),
  markChapter: (
    sessionId: string,
    themeId: string,
    documentId: string,
    chapterId: string,
    done: boolean,
  ) =>
    request<LearningSession>(
      `/api/learning-sessions/${sessionId}/chapters/${themeId}/${documentId}/${chapterId}`,
      { method: "POST", body: JSON.stringify({ done }) },
    ),
  submitExercise: (id: string, blockId: string, answer: string) =>
    request<{ feedback: string }>(
      `/api/learning-sessions/${id}/exercises/${blockId}`,
      { method: "POST", body: JSON.stringify({ answer }) },
    ),
  research: () => request<ResearchJob[]>("/api/research"),
  researchJob: (id: string) => request<ResearchJob>(`/api/research/${id}`),
  createResearch: (data: {
    topic: string;
    urls: string[];
    depth: string;
    level: string;
    goal: string;
    material_type: "study_guide" | "learning_companion";
    source_document_id?: string;
  }) =>
    request<ResearchJob>("/api/research", {
      method: "POST",
      body: JSON.stringify(data),
      // A retried or double-clicked submit returns the same project instead of a second one.
      headers: { "Idempotency-Key": crypto.randomUUID() },
    }),
  retryResearch: (id: string) =>
    request<ResearchJob>(`/api/research/${id}/retry`, { method: "POST" }),
  cancelResearch: (id: string) =>
    request<ResearchJob>(`/api/research/${id}/cancel`, { method: "POST" }),
  publishResearch: (id: string) =>
    request<ResearchJob>(`/api/research/${id}/publish`, { method: "POST" }),
  selectResource: (jobId: string, resourceId: string, selected: boolean) =>
    request<ResearchJob>(
      `/api/research/${jobId}/resources/${resourceId}/selection`,
      {
        method: "POST",
        body: JSON.stringify({ selected }),
      },
    ),
  tutors: () => request<TutorSettings>("/api/tutors"),
  testTutor: (data: ConnectionDraft) =>
    request<ConnectionTest>("/api/tutors/test", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  saveTutor: (data: ConnectionDraft) =>
    request<{ provider: TutorProvider }>("/api/tutors", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  activateTutor: (id: string) =>
    request<{ providers: TutorProvider[]; catalog: Catalog }>(
      `/api/tutors/${id}/activate`,
      { method: "POST" },
    ),
  deleteTutor: (id: string) =>
    request<{ providers: TutorProvider[] }>(`/api/tutors/${id}`, {
      method: "DELETE",
    }),
  activateModel: (name: string) =>
    request<{ providers: TutorProvider[]; catalog: Catalog }>(
      `/api/tutors/models/${name}/activate`,
      { method: "POST" },
    ),
  deleteModel: (name: string) =>
    request<{ catalog: Catalog }>(`/api/tutors/models/${name}`, {
      method: "DELETE",
    }),
  async pullModel(name: string, onProgress: (p: PullProgress) => void) {
    const response = await fetch(base + "/api/tutors/models/pull", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...CSRF },
      body: JSON.stringify({ name }),
    });
    if (!response.ok || !response.body)
      throw new Error("The download could not be started.");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        if (block.startsWith("data: "))
          onProgress(JSON.parse(block.slice(6)) as PullProgress);
      }
    }
  },
  documents: () => request<Document[]>("/api/documents"),
  document: (id: string) => request<Document>("/api/documents/" + id),
  reader: (id: string) => request<ReaderInfo>(`/api/documents/${id}/reader`),
  cover: (id: string) => `${base}/api/documents/${id}/preview`,
  source: (id: string) => `${base}/api/documents/${id}/source`,
  refresh: () =>
    request<{ warnings: string[] }>("/api/library/refresh", { method: "POST" }),
  trash: (id: string) =>
    request<void>(`/api/documents/${id}`, { method: "DELETE" }),
  restore: (id: string) =>
    request<Document>(`/api/documents/${id}/restore`, { method: "POST" }),
  sessions: () => request<Session[]>("/api/sessions"),
  session: (id: string) => request<Session>(`/api/sessions/${id}`),
  openSession: (document_id: string, fresh = false) =>
    request<Session>("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ document_id, new: fresh }),
    }),
  save: (id: string, values: Partial<Session>) =>
    request<Session>(`/api/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify(values),
    }),
  models: async () => (await request<{ tutor: Catalog }>("/api/config")).tutor,
  refreshModels: () =>
    request<Catalog>("/api/models/refresh", { method: "POST" }),
  upload: (file: File, progress: (percent: number) => void) =>
    new Promise<void>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", base + "/api/documents/upload");
      xhr.setRequestHeader("X-StudyHub-CSRF", "1");
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable)
          progress(Math.round((e.loaded / e.total) * 100));
      };
      xhr.onerror = () =>
        reject(new Error("Upload interrupted. Please try again."));
      xhr.onload = () => {
        try {
          if (xhr.status === 401) signedOut();
          const data = JSON.parse(xhr.responseText);
          if (xhr.status >= 400 || data.warnings?.length)
            reject(
              new Error(
                data.detail || data.warnings?.join("\n") || "Upload failed",
              ),
            );
          else resolve();
        } catch {
          reject(new Error("Upload failed. Please try again."));
        }
      };
      const form = new FormData();
      form.append("file", file);
      xhr.send(form);
    }),
  async chat(
    sid: string,
    text: string,
    selected_text: string,
    intent: string,
    signal: AbortSignal,
    onPart: (part: StreamPart) => void,
  ) {
    const response = await fetch(`${base}/api/sessions/${sid}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...CSRF },
      body: JSON.stringify({ text, selected_text, intent }),
      signal,
    });
    if (response.status === 401) signedOut();
    if (!response.ok) {
      const data = await response.json();
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : "Could not start this reply.",
      );
    }
    if (!response.body) throw new Error("Streaming is unavailable.");
    const reader = response.body.getReader(),
      decoder = new TextDecoder();
    let buffer = "",
      finished = false;
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary;
        while ((boundary = buffer.indexOf("\n\n")) >= 0) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          if (block.startsWith("data: ")) {
            const part = JSON.parse(block.slice(6)) as StreamPart;
            if (part.type === "done" || part.type === "error") finished = true;
            onPart(part);
          }
        }
      }
      if (!finished) throw new Error("The response ended early. Please retry.");
    } finally {
      reader.releaseLock();
    }
  },
};
