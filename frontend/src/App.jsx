import { useState, useEffect } from 'react';
import Sidebar from './components/Sidebar';
import ChatInterface from './components/ChatInterface';
import { api } from './api';
import './App.css';

function mergeMessageMetadata(existingMetadata, incomingMetadata) {
  if (!incomingMetadata) {
    return existingMetadata ?? null;
  }

  return {
    ...(existingMetadata ?? {}),
    ...incomingMetadata,
  };
}

const STREAM_STAGES = {
  stage0: {
    label: 'Stage 0',
    running: 'Reformulating this follow-up into a standalone question',
    complete: 'Question reformulated for the council',
  },
  stage1: {
    label: 'Stage 1',
    running: 'Collecting individual council responses',
    complete: 'Individual responses collected',
  },
  stage2: {
    label: 'Stage 2',
    running: 'Collecting anonymous peer rankings',
    complete: 'Peer rankings collected',
  },
  stage2a: {
    label: 'Stage 2a',
    running: 'Collecting peer critiques',
    complete: 'Peer critiques collected',
  },
  stage2b: {
    label: 'Stage 2b',
    running: 'Collecting revised responses',
    complete: 'Revisions collected',
  },
  stage3: {
    label: 'Stage 3',
    running: 'Synthesizing the final council answer',
    complete: 'Final synthesis complete',
  },
};

function updateProgressStep(progress, stageKey, status, detail) {
  const stage = STREAM_STAGES[stageKey] ?? { label: stageKey };
  const nextStep = {
    key: stageKey,
    label: stage.label,
    status,
    detail: detail ?? stage[status],
  };
  const existing = progress ?? [];
  const index = existing.findIndex((step) => step.key === stageKey);

  if (index === -1) {
    return [...existing, nextStep];
  }

  return existing.map((step, stepIndex) => (
    stepIndex === index ? nextStep : step
  ));
}

function createStreamingAssistantMessage(streamId) {
  return {
    role: 'assistant',
    streamId,
    stage1: null,
    stage2: null,
    stage2a: null,
    stage2b: null,
    stage3: null,
    metadata: null,
    progress: [],
    loading: {
      stage0: false,
      stage1: false,
      stage2: false,
      stage2a: false,
      stage2b: false,
      stage3: false,
    },
  };
}

function updateStreamingAssistantMessage(conversation, conversationId, streamId, updateMessage) {
  if (!conversation || conversation.id !== conversationId) {
    return conversation;
  }

  const messages = [...conversation.messages];
  let index = messages.findIndex(
    (message) => message.role === 'assistant' && message.streamId === streamId
  );

  if (index === -1) {
    index = messages.length;
    messages.push(createStreamingAssistantMessage(streamId));
  }

  const lastMsg = { ...messages[index] };
  updateMessage(lastMsg);
  messages[index] = lastMsg;
  return { ...conversation, messages };
}

function markStage(lastMsg, stageKey, status, detail) {
  lastMsg.loading = {
    ...(lastMsg.loading ?? {}),
    [stageKey]: status === 'running',
  };
  lastMsg.progress = updateProgressStep(
    lastMsg.progress,
    stageKey,
    status,
    detail
  );
}

function markStreamError(lastMsg, message) {
  const progress = lastMsg.progress ?? [];
  const runningIndex = progress.findIndex((step) => step.status === 'running');
  const failedStep = {
    key: runningIndex === -1 ? 'stream-error' : progress[runningIndex].key,
    label: runningIndex === -1 ? 'Stream' : progress[runningIndex].label,
    status: 'failed',
    detail: message ? `Stream error: ${message}` : 'Stream interrupted before completion',
  };

  if (runningIndex === -1) {
    lastMsg.progress = [...progress, failedStep];
  } else {
    lastMsg.progress = progress.map((step, index) => (
      index === runningIndex ? failedStep : step
    ));
  }

  lastMsg.loading = Object.fromEntries(
    Object.keys(lastMsg.loading ?? {}).map((key) => [key, false])
  );
}

function rollbackUnsentMessage(conversation, conversationId, streamId, content) {
  if (!conversation || conversation.id !== conversationId) {
    return conversation;
  }

  const messages = [...conversation.messages];
  const assistantIndex = messages.findIndex(
    (message) => message.role === 'assistant' && message.streamId === streamId
  );

  if (assistantIndex === -1) {
    return conversation;
  }

  messages.splice(assistantIndex, 1);
  const userIndex = assistantIndex - 1;
  if (messages[userIndex]?.role === 'user' && messages[userIndex].content === content) {
    messages.splice(userIndex, 1);
  }

  return { ...conversation, messages };
}

function App() {
  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(null);
  const [currentConversation, setCurrentConversation] = useState(null);
  const [loadingConversationId, setLoadingConversationId] = useState(null);

  async function loadConversations() {
    try {
      const convs = await api.listConversations();
      setConversations(convs);
    } catch (error) {
      console.error('Failed to load conversations:', error);
    }
  }

  // Load conversations on mount
  useEffect(() => {
    let cancelled = false;

    api.listConversations()
      .then((convs) => {
        if (!cancelled) {
          setConversations(convs);
        }
      })
      .catch((error) => {
        console.error('Failed to load conversations:', error);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // Load conversation details when selected
  useEffect(() => {
    if (currentConversationId) {
      let cancelled = false;

      api.getConversation(currentConversationId)
        .then((conv) => {
          if (!cancelled) {
            setCurrentConversation(conv);
          }
        })
        .catch((error) => {
          console.error('Failed to load conversation:', error);
        });

      return () => {
        cancelled = true;
      };
    }
  }, [currentConversationId]);

  const handleNewConversation = async () => {
    try {
      const newConv = await api.createConversation();
      setConversations([
        { id: newConv.id, created_at: newConv.created_at, message_count: 0 },
        ...conversations,
      ]);
      setCurrentConversationId(newConv.id);
    } catch (error) {
      console.error('Failed to create conversation:', error);
    }
  };

  const handleSelectConversation = (id) => {
    setCurrentConversationId(id);
  };

  const handleSendMessage = async (content) => {
    if (!currentConversationId) return;
    const activeConversationId = currentConversationId;
    const streamId = crypto.randomUUID();
    let receivedStreamEvent = false;

    setLoadingConversationId(activeConversationId);
    try {
      // Optimistically add user message to UI
      const userMessage = { role: 'user', content };
      setCurrentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, userMessage],
      }));

      // Create a partial assistant message that will be updated progressively
      const assistantMessage = createStreamingAssistantMessage(streamId);

      // Add the partial assistant message
      setCurrentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, assistantMessage],
      }));

      // Send message with streaming
      await api.sendMessageStream(activeConversationId, content, (eventType, event) => {
        receivedStreamEvent = true;
        switch (eventType) {
          case 'stage0_start':
            setCurrentConversation((prev) => updateStreamingAssistantMessage(prev, activeConversationId, streamId, (lastMsg) => {
              markStage(lastMsg, 'stage0', 'running');
            }));
            break;

          case 'stage0_complete':
            setCurrentConversation((prev) => updateStreamingAssistantMessage(prev, activeConversationId, streamId, (lastMsg) => {
              lastMsg.metadata = mergeMessageMetadata(lastMsg.metadata, {
                stage0_standalone_query: event.data?.standalone_query,
              });
              markStage(lastMsg, 'stage0', 'complete');
            }));
            break;

          case 'stage1_start':
            setCurrentConversation((prev) => updateStreamingAssistantMessage(prev, activeConversationId, streamId, (lastMsg) => {
              markStage(lastMsg, 'stage1', 'running');
            }));
            break;

          case 'stage1_complete':
            setCurrentConversation((prev) => updateStreamingAssistantMessage(prev, activeConversationId, streamId, (lastMsg) => {
              lastMsg.stage1 = event.data;
              markStage(lastMsg, 'stage1', 'complete');
            }));
            break;

          case 'stage2_start':
            setCurrentConversation((prev) => updateStreamingAssistantMessage(prev, activeConversationId, streamId, (lastMsg) => {
              markStage(lastMsg, 'stage2', 'running');
            }));
            break;

          case 'stage2_complete':
            setCurrentConversation((prev) => updateStreamingAssistantMessage(prev, activeConversationId, streamId, (lastMsg) => {
              lastMsg.stage2 = event.data;
              lastMsg.metadata = mergeMessageMetadata(lastMsg.metadata, event.metadata);
              markStage(lastMsg, 'stage2', 'complete');
            }));
            break;

          case 'stage2a_start':
            setCurrentConversation((prev) => updateStreamingAssistantMessage(prev, activeConversationId, streamId, (lastMsg) => {
              markStage(lastMsg, 'stage2a', 'running');
            }));
            break;

          case 'stage2a_complete':
            setCurrentConversation((prev) => updateStreamingAssistantMessage(prev, activeConversationId, streamId, (lastMsg) => {
              lastMsg.stage2a = event.data;
              lastMsg.metadata = mergeMessageMetadata(lastMsg.metadata, event.metadata);
              markStage(lastMsg, 'stage2a', 'complete');
            }));
            break;

          case 'stage2b_start':
            setCurrentConversation((prev) => updateStreamingAssistantMessage(prev, activeConversationId, streamId, (lastMsg) => {
              markStage(lastMsg, 'stage2b', 'running');
            }));
            break;

          case 'stage2b_complete':
            setCurrentConversation((prev) => updateStreamingAssistantMessage(prev, activeConversationId, streamId, (lastMsg) => {
              lastMsg.stage2b = event.data;
              lastMsg.metadata = mergeMessageMetadata(lastMsg.metadata, event.metadata);
              markStage(lastMsg, 'stage2b', 'complete');
            }));
            break;

          case 'stage3_start':
            setCurrentConversation((prev) => updateStreamingAssistantMessage(prev, activeConversationId, streamId, (lastMsg) => {
              markStage(lastMsg, 'stage3', 'running');
            }));
            break;

          case 'stage3_complete':
            setCurrentConversation((prev) => updateStreamingAssistantMessage(prev, activeConversationId, streamId, (lastMsg) => {
              lastMsg.stage3 = event.data;
              lastMsg.metadata = mergeMessageMetadata(lastMsg.metadata, event.metadata);
              markStage(lastMsg, 'stage3', 'complete');
            }));
            break;

          case 'title_complete':
            // Reload conversations to get updated title
            loadConversations();
            break;

          case 'complete':
            // Stream complete, reload conversations list
            loadConversations();
            setLoadingConversationId((id) => (
              id === activeConversationId ? null : id
            ));
            break;

          case 'error':
            console.error('Stream error:', event.message);
            setCurrentConversation((prev) => updateStreamingAssistantMessage(prev, activeConversationId, streamId, (lastMsg) => {
              markStreamError(lastMsg, event.message);
            }));
            setLoadingConversationId((id) => (
              id === activeConversationId ? null : id
            ));
            break;

          default:
            console.log('Unknown event type:', eventType);
        }
      });
      setLoadingConversationId((id) => (
        id === activeConversationId ? null : id
      ));
    } catch (error) {
      console.error('Failed to send message:', error);
      if (receivedStreamEvent || error.streamStarted) {
        setCurrentConversation((prev) => updateStreamingAssistantMessage(prev, activeConversationId, streamId, (lastMsg) => {
          markStreamError(lastMsg, error.message);
        }));
      } else {
        setCurrentConversation((prev) => rollbackUnsentMessage(
          prev,
          activeConversationId,
          streamId,
          content
        ));
      }
      setLoadingConversationId((id) => (
        id === activeConversationId ? null : id
      ));
    }
  };

  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        currentConversationId={currentConversationId}
        onSelectConversation={handleSelectConversation}
        onNewConversation={handleNewConversation}
      />
      <ChatInterface
        conversation={currentConversation}
        onSendMessage={handleSendMessage}
        isLoading={loadingConversationId === currentConversationId}
      />
    </div>
  );
}

export default App;
