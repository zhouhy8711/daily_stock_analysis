import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAgentChatStore } from '../agentChatStore';

vi.mock('../../api/agent', () => ({
  agentApi: {
    getChatSessions: vi.fn(async () => []),
    getChatSessionMessages: vi.fn(async () => []),
    chatStream: vi.fn(),
  },
}));

const { agentApi } = await import('../../api/agent');

const encoder = new TextEncoder();

function createStreamResponse(lines: string[]) {
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(lines.join('\n')));
        controller.close();
      },
    }),
    {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    },
  );
}

describe('agentChatStore.startStream', () => {
  beforeEach(() => {
    localStorage.clear();
    useAgentChatStore.setState({
      messages: [],
      loading: false,
      progressSteps: [],
      sessionId: 'session-test',
      sessions: [],
      sessionsLoading: false,
      chatError: null,
      currentRoute: '/chat',
      completionBadge: false,
      hasInitialLoad: true,
      abortController: null,
      activeStreams: {},
    });
    vi.clearAllMocks();
  });

  it('appends the user message and final assistant message from the SSE stream', async () => {
    vi.mocked(agentApi.chatStream).mockResolvedValue(
      createStreamResponse([
        'data: {"type":"thinking","step":1,"message":"分析中"}',
        'data: {"type":"tool_done","tool":"quote","display_name":"行情","success":true,"duration":0.3}',
        'data: {"type":"done","success":true,"content":"最终分析结果"}',
      ]),
    );

    await useAgentChatStore
      .getState()
      .startStream({ message: '分析茅台', session_id: 'session-test' }, { skillName: '趋势技能' });

    const state = useAgentChatStore.getState();
    expect(state.loading).toBe(false);
    expect(state.chatError).toBeNull();
    expect(state.messages).toHaveLength(2);
    expect(state.messages[0]).toMatchObject({
      role: 'user',
      content: '分析茅台',
      skillName: '趋势技能',
    });
    expect(state.messages[1]).toMatchObject({
      role: 'assistant',
      content: '最终分析结果',
      skillName: '趋势技能',
    });
    expect(state.messages[1].thinkingSteps).toHaveLength(2);
    expect(state.progressSteps).toEqual([]);
  });

  it('keeps in-flight progress scoped to its own session', async () => {
    const firstStream: { controller?: ReadableStreamDefaultController<Uint8Array> } = {};
    vi.mocked(agentApi.chatStream)
      .mockResolvedValueOnce(new Response(
        new ReadableStream({
          start(controller) {
            firstStream.controller = controller;
            controller.enqueue(encoder.encode('data: {"type":"thinking","step":1,"message":"获取实时行情..."}\n'));
          },
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        },
      ))
      .mockResolvedValueOnce(createStreamResponse([
        'data: {"type":"done","success":true,"content":"第二个会话结果"}',
      ]));

    const firstRun = useAgentChatStore
      .getState()
      .startStream({ message: '分析中际旭创', session_id: 'session-test' }, { skillName: '通用' });

    await vi.waitFor(() => {
      const state = useAgentChatStore.getState();
      expect(state.loading).toBe(true);
      expect(state.progressSteps[0]?.message).toBe('获取实时行情...');
    });

    useAgentChatStore.getState().startNewChat();
    const newSessionId = useAgentChatStore.getState().sessionId;
    expect(useAgentChatStore.getState().loading).toBe(false);
    expect(useAgentChatStore.getState().progressSteps).toEqual([]);

    await useAgentChatStore
      .getState()
      .startStream({ message: '看一下 600519', session_id: newSessionId }, { skillName: '通用' });

    let state = useAgentChatStore.getState();
    expect(state.sessionId).toBe(newSessionId);
    expect(state.messages.map((msg) => msg.content)).toEqual([
      '看一下 600519',
      '第二个会话结果',
    ]);
    expect(state.progressSteps).toEqual([]);

    firstStream.controller?.enqueue(encoder.encode('data: {"type":"done","success":true,"content":"第一个会话结果"}\n'));
    firstStream.controller?.close();
    await firstRun;

    state = useAgentChatStore.getState();
    expect(state.sessionId).toBe(newSessionId);
    expect(state.messages.map((msg) => msg.content)).toEqual([
      '看一下 600519',
      '第二个会话结果',
    ]);
    expect(state.activeStreams).toEqual({});
  });
});
