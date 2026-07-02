import { useState, useEffect, useId, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import Stage1 from './Stage1';
import Stage2 from './Stage2';
import Stage3 from './Stage3';
import './ChatInterface.css';

function CouncilProgress({ progress, councilConfidence, finalResponse }) {
  if (!progress || progress.length === 0) {
    return null;
  }

  const isRunning = progress.some((step) => step.status === 'running');
  const hasFailed = progress.some((step) => step.status === 'failed');
  const latestStep = progress[progress.length - 1];

  return (
    <div className="council-progress">
      <div className="council-progress-live" role="status" aria-live="polite">
        {latestStep.label}: {latestStep.detail}
      </div>
      <div className="council-progress-header">
        <span className="council-progress-title">Council progress</span>
        <span className="council-progress-state">
          {hasFailed ? 'Interrupted' : isRunning ? 'In progress' : 'Complete'}
        </span>
      </div>
      <ol className="council-progress-list">
        {progress.map((step) => (
          <li key={step.key} className={`council-progress-step ${step.status}`}>
            <span className="council-progress-marker" aria-hidden="true">
              {step.status === 'running' ? '' : step.status === 'failed' ? '!' : '✓'}
            </span>
            <span className="council-progress-copy">
              <span className="council-progress-label">{step.label}</span>
              <span className="council-progress-detail">{step.detail}</span>
            </span>
          </li>
        ))}
      </ol>
      {councilConfidence?.low_confidence && !finalResponse && (
        <div className="progress-confidence-warning">
          Stage 2 found split rankings. The chairman will separate shared findings
          from contested claims before the final answer appears.
        </div>
      )}
    </div>
  );
}

function shortModelName(model) {
  return model?.split('/')[1] || model;
}

function deAnonymizeText(text, labelToModel) {
  if (!labelToModel || !text) return text;

  let result = text;
  Object.entries(labelToModel).forEach(([label, model]) => {
    result = result.replace(new RegExp(label, 'g'), `**${shortModelName(model)}**`);
  });
  return result;
}

function deAnonymizeLabel(label, labelToModel) {
  if (!labelToModel || !labelToModel[label]) {
    return label;
  }

  return shortModelName(labelToModel[label]);
}

function StageDetails({ title, description, items, contentField, labelToModel }) {
  const [activeTab, setActiveTab] = useState(0);
  const baseId = useId();

  if (!items || items.length === 0) {
    return null;
  }

  const selectTabFromKey = (event, index) => {
    const lastIndex = items.length - 1;
    let nextIndex = index;

    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      nextIndex = index === lastIndex ? 0 : index + 1;
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      nextIndex = index === 0 ? lastIndex : index - 1;
    } else if (event.key === 'Home') {
      nextIndex = 0;
    } else if (event.key === 'End') {
      nextIndex = lastIndex;
    } else {
      return;
    }

    event.preventDefault();
    setActiveTab(nextIndex);
    const tabs = event.currentTarget.parentElement?.querySelectorAll('[role="tab"]');
    tabs?.[nextIndex]?.focus();
  };

  return (
    <div className="stage stage-details">
      <h3 className="stage-title">{title}</h3>
      <p className="stage-description">{description}</p>

      <div className="tabs" role="tablist" aria-label={title}>
        {items.map((item, index) => (
          <button
            key={index}
            id={`${baseId}-tab-${index}`}
            type="button"
            role="tab"
            aria-selected={activeTab === index}
            aria-controls={`${baseId}-panel-${index}`}
            tabIndex={activeTab === index ? 0 : -1}
            className={`tab ${activeTab === index ? 'active' : ''}`}
            onClick={() => setActiveTab(index)}
            onKeyDown={(event) => selectTabFromKey(event, index)}
          >
            {shortModelName(item.model) || `Result ${index + 1}`}
          </button>
        ))}
      </div>

      {items.map((item, index) => (
        <div
          key={index}
          id={`${baseId}-panel-${index}`}
          className="tab-content"
          role="tabpanel"
          aria-labelledby={`${baseId}-tab-${index}`}
          tabIndex={0}
          hidden={activeTab !== index}
        >
          {activeTab === index && (
            <>
              <div className="model-name">{item.model}</div>
              {item.original_label && (
                <div className="stage-details-label">
                  {deAnonymizeLabel(item.original_label, labelToModel)}
                </div>
              )}
              <div className="response-text markdown-content">
                <ReactMarkdown>
                  {deAnonymizeText(item[contentField], labelToModel) ?? ''}
                </ReactMarkdown>
              </div>
            </>
          )}
        </div>
      ))}
    </div>
  );
}

export default function ChatInterface({
  conversation,
  onSendMessage,
  isLoading,
}) {
  const [input, setInput] = useState('');
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [conversation]);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (input.trim() && !isLoading) {
      onSendMessage(input);
      setInput('');
    }
  };

  const handleKeyDown = (e) => {
    // Submit on Enter (without Shift)
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  if (!conversation) {
    return (
      <div className="chat-interface">
        <div className="empty-state">
          <h2>Welcome to LLM Council</h2>
          <p>Create a new conversation to get started</p>
        </div>
      </div>
    );
  }

  const hasDetailedProgress = conversation.messages.some(
    (msg) => msg.role === 'assistant' && msg.progress?.some((step) => step.status === 'running')
  );

  return (
    <div className="chat-interface">
      <div className="messages-container">
        {conversation.messages.length === 0 ? (
          <div className="empty-state">
            <h2>Start a conversation</h2>
            <p>Ask a question to consult the LLM Council</p>
          </div>
        ) : (
          conversation.messages.map((msg, index) => (
            <div key={index} className="message-group">
              {msg.role === 'user' ? (
                <div className="user-message">
                  <div className="message-label">You</div>
                  <div className="message-content">
                    <div className="markdown-content">
                      <ReactMarkdown>{msg.content}</ReactMarkdown>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="assistant-message">
                  <div className="message-label">LLM Council</div>

                  <CouncilProgress
                    progress={msg.progress}
                    councilConfidence={msg.metadata?.council_confidence}
                    finalResponse={msg.stage3}
                  />

                  {/* Stage 1 */}
                  {msg.stage1 && <Stage1 responses={msg.stage1} />}

                  {/* Stage 2 */}
                  {msg.stage2 && (
                    <Stage2
                      rankings={msg.stage2}
                      labelToModel={msg.metadata?.label_to_model}
                      aggregateRankings={msg.metadata?.aggregate_rankings}
                    />
                  )}

                  {msg.stage2a && (
                    <StageDetails
                      title="Stage 2a: Peer Critiques"
                      description="Thorough mode critiques each anonymized response before revisions."
                      items={msg.stage2a}
                      contentField="critiques"
                      labelToModel={msg.metadata?.label_to_model}
                    />
                  )}

                  {msg.stage2b && (
                    <StageDetails
                      title="Stage 2b: Revised Responses"
                      description="Each council member revises its own answer using the peer critiques."
                      items={msg.stage2b}
                      contentField="revision"
                      labelToModel={msg.metadata?.label_to_model}
                    />
                  )}

                  {/* Stage 3 */}
                  {msg.stage3 && (
                    <Stage3
                      finalResponse={msg.stage3}
                      runStatus={msg.metadata?.run_status}
                      councilConfidence={msg.metadata?.council_confidence}
                      labelToModel={msg.metadata?.label_to_model}
                    />
                  )}
                </div>
              )}
            </div>
          ))
        )}

        {isLoading && !hasDetailedProgress && (
          <div className="loading-indicator">
            <div className="spinner"></div>
            <span>Consulting the council...</span>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {conversation.messages.length === 0 && (
        <form className="input-form" onSubmit={handleSubmit}>
          <textarea
            className="message-input"
            placeholder="Ask your question... (Shift+Enter for new line, Enter to send)"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isLoading}
            rows={3}
          />
          <button
            type="submit"
            className="send-button"
            disabled={!input.trim() || isLoading}
          >
            Send
          </button>
        </form>
      )}
    </div>
  );
}
