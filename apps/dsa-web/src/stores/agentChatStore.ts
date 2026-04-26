import { create } from 'zustand';
import { agentApi } from '../api/agent';
import type { ChatSessionItem, ChatStreamRequest } from '../api/agent';
import {
  createParsedApiError,
  getParsedApiError,
  isApiRequestError,
  isParsedApiError,
  type ParsedApiError,
} from '../api/error';
import { generateUUID } from '../utils/uuid';

const STORAGE_KEY_SESSION = 'dsa_chat_session_id';

export interface ProgressStep {
  type: string;
  step?: number;
  tool?: string;
  display_name?: string;
  success?: boolean;
  duration?: number;
  message?: string;
  content?: string;
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  skill?: string;
  skillName?: string;
  thinkingSteps?: ProgressStep[];
}

export interface StreamMeta {
  skillName?: string;
}

interface ActiveStreamInfo {
  progressSteps: ProgressStep[];
  abortController: AbortController;
}

interface AgentChatState {
  messages: Message[];
  loading: boolean;
  progressSteps: ProgressStep[];
  sessionId: string;
  sessions: ChatSessionItem[];
  sessionsLoading: boolean;
  chatError: ParsedApiError | null;
  currentRoute: string;
  completionBadge: boolean;
  hasInitialLoad: boolean;
  abortController: AbortController | null;
  activeStreams: Record<string, ActiveStreamInfo>;
}

interface AgentChatActions {
  setCurrentRoute: (path: string) => void;
  clearCompletionBadge: () => void;
  loadSessions: () => Promise<void>;
  loadInitialSession: () => Promise<void>;
  switchSession: (targetSessionId: string) => Promise<void>;
  startNewChat: () => void;
  startStream: (payload: ChatStreamRequest, meta?: StreamMeta) => Promise<void>;
}

const getInitialSessionId = (): string =>
  typeof localStorage !== 'undefined'
    ? localStorage.getItem(STORAGE_KEY_SESSION) || generateUUID()
    : generateUUID();

export const useAgentChatStore = create<AgentChatState & AgentChatActions>((set, get) => ({
  messages: [],
  loading: false,
  progressSteps: [],
  sessionId: getInitialSessionId(),
  sessions: [],
  sessionsLoading: false,
  chatError: null,
  currentRoute: '',
  completionBadge: false,
  hasInitialLoad: false,
  abortController: null,
  activeStreams: {},

  setCurrentRoute: (path) => set({ currentRoute: path }),

  clearCompletionBadge: () => set({ completionBadge: false }),

  loadSessions: async () => {
    set({ sessionsLoading: true });
    try {
      const sessions = await agentApi.getChatSessions();
      set({ sessions });
    } catch {
      // Ignore load errors
    } finally {
      set({ sessionsLoading: false });
    }
  },

  loadInitialSession: async () => {
    const { hasInitialLoad } = get();
    if (hasInitialLoad) return;
    set({ hasInitialLoad: true, sessionsLoading: true });

    try {
      const sessionList = await agentApi.getChatSessions();
      set({ sessions: sessionList });

      const savedId = localStorage.getItem(STORAGE_KEY_SESSION);
      if (savedId) {
        const sessionExists = sessionList.some((s) => s.session_id === savedId);
        if (sessionExists) {
          const msgs = await agentApi.getChatSessionMessages(savedId);
          if (msgs.length > 0) {
            set({
              messages: msgs.map((m) => ({
                id: m.id,
                role: m.role,
                content: m.content,
              })),
            });
          }
        } else {
          const newId = generateUUID();
          set({ sessionId: newId });
          localStorage.setItem(STORAGE_KEY_SESSION, newId);
        }
      } else {
        localStorage.setItem(STORAGE_KEY_SESSION, get().sessionId);
      }
    } catch {
      // Ignore
    } finally {
      set({ sessionsLoading: false });
    }
  },

  switchSession: async (targetSessionId) => {
    const { sessionId, messages } = get();
    if (targetSessionId === sessionId && messages.length > 0) return;

    const activeStream = get().activeStreams[targetSessionId];
    set({
      messages: [],
      sessionId: targetSessionId,
      loading: Boolean(activeStream),
      progressSteps: activeStream?.progressSteps ?? [],
      abortController: activeStream?.abortController ?? null,
      chatError: null,
    });
    localStorage.setItem(STORAGE_KEY_SESSION, targetSessionId);

    try {
      const msgs = await agentApi.getChatSessionMessages(targetSessionId);
      const latest = get();
      if (latest.sessionId !== targetSessionId) return;
      const latestActiveStream = latest.activeStreams[targetSessionId];
      set({
        messages: msgs.map((m) => ({
          id: m.id,
          role: m.role,
          content: m.content,
        })),
        loading: Boolean(latestActiveStream),
        progressSteps: latestActiveStream?.progressSteps ?? [],
        abortController: latestActiveStream?.abortController ?? null,
      });
    } catch {
      // Ignore
    }
  },

  startNewChat: () => {
    const newId = generateUUID();
    set({
      sessionId: newId,
      messages: [],
      loading: false,
      progressSteps: [],
      chatError: null,
      abortController: null,
    });
    localStorage.setItem(STORAGE_KEY_SESSION, newId);
  },

  startStream: async (payload, meta) => {
    const { activeStreams, sessionId: storeSessionId } = get();
    const streamSessionId = payload.session_id || storeSessionId;
    if (activeStreams[streamSessionId]) return;

    const ac = new AbortController();
    const skillName = meta?.skillName ?? '通用';

    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: payload.message,
      skill: payload.skills?.[0],
      skillName,
    };

    set((s) => ({
      activeStreams: {
        ...s.activeStreams,
        [streamSessionId]: {
          progressSteps: [],
          abortController: ac,
        },
      },
      messages: s.sessionId === streamSessionId ? [...s.messages, userMessage] : s.messages,
      loading: s.sessionId === streamSessionId ? true : s.loading,
      progressSteps: s.sessionId === streamSessionId ? [] : s.progressSteps,
      abortController: s.sessionId === streamSessionId ? ac : s.abortController,
      chatError: s.sessionId === streamSessionId ? null : s.chatError,
      sessions: s.sessions.some((x) => x.session_id === streamSessionId)
        ? s.sessions
        : [
            {
              session_id: streamSessionId,
              title: payload.message.slice(0, 60),
              message_count: 1,
              created_at: new Date().toISOString(),
              last_active: new Date().toISOString(),
            },
            ...s.sessions,
          ],
    }));

    try {
      const response = await agentApi.chatStream(payload, { signal: ac.signal });
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      let finalContent: string | null = null;
      const currentProgressSteps: ProgressStep[] = [];
      const processLine = (line: string) => {
        if (!line.startsWith('data: ')) return;

        const event = JSON.parse(line.slice(6)) as ProgressStep;
        if (event.type === 'done') {
          const doneEvent = event as unknown as {
            type: string;
            success: boolean;
            content?: string;
            error?: string;
          };
          if (doneEvent.success === false) {
            const parsedStreamError = getParsedApiError(
              doneEvent.error ||
                doneEvent.content ||
                '大模型调用出错，请检查 API Key 配置',
            );
            throw createParsedApiError({
              title: '问股执行失败',
              message: parsedStreamError.message,
              rawMessage: parsedStreamError.rawMessage,
              status: parsedStreamError.status,
              category: parsedStreamError.category,
            });
          }
          finalContent = doneEvent.content ?? '';
          return;
        }

        if (event.type === 'error') {
          throw getParsedApiError(event.message || '分析出错');
        }

        currentProgressSteps.push(event);
        set((s) => {
          const activeStream = s.activeStreams[streamSessionId];
          if (!activeStream) return {};
          const nextSteps = [...activeStream.progressSteps, event];
          const activeStreams = {
            ...s.activeStreams,
            [streamSessionId]: {
              ...activeStream,
              progressSteps: nextSteps,
            },
          };
          if (s.sessionId !== streamSessionId) {
            return { activeStreams };
          }
          return {
            activeStreams,
            progressSteps: nextSteps,
          };
        });
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() ?? '';

        for (const line of lines) {
          try {
            processLine(line);
          } catch (parseErr: unknown) {
            if (isParsedApiError(parseErr) || isApiRequestError(parseErr)) {
              throw parseErr;
            }
          }
        }
      }

      if (buf.trim().startsWith('data: ')) {
        try {
          processLine(buf.trim());
        } catch (parseErr: unknown) {
          if (isParsedApiError(parseErr) || isApiRequestError(parseErr)) {
            throw parseErr;
          }
        }
      }

      const { sessionId: currentSessionId, currentRoute } = get();
      const shouldAppend =
        currentSessionId === streamSessionId && !ac.signal.aborted;

      if (shouldAppend) {
        set((s) => ({
          messages: [
            ...s.messages,
            {
              id: (Date.now() + 1).toString(),
              role: 'assistant',
              content: finalContent || '（无内容）',
              skill: payload.skills?.[0],
              skillName,
              thinkingSteps: [...currentProgressSteps],
            },
          ],
        }));
      }

      if (currentRoute !== '/chat') {
        set({ completionBadge: true });
      }
    } catch (error: unknown) {
      if (error instanceof Error && error.name === 'AbortError') {
        // User-initiated abort: silent, no badge
      } else {
        const { currentRoute, sessionId: currentSessionId } = get();
        if (currentSessionId === streamSessionId) {
          set({ chatError: getParsedApiError(error) });
        }
        if (currentRoute !== '/chat' || currentSessionId !== streamSessionId) {
          set({ completionBadge: true });
        }
      }
    } finally {
      set((s) => {
        const activeStream = s.activeStreams[streamSessionId];
        if (activeStream?.abortController !== ac) return {};
        const activeStreams = { ...s.activeStreams };
        delete activeStreams[streamSessionId];
        if (s.sessionId !== streamSessionId) {
          return {
            activeStreams,
            abortController: s.abortController === ac ? null : s.abortController,
          };
        }
        return {
          activeStreams,
          loading: false,
          progressSteps: [],
          abortController: null,
        };
      });
      await get().loadSessions();
    }
  },
}));
