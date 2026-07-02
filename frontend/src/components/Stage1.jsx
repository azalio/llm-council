import { useId, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import './Stage1.css';

export default function Stage1({ responses }) {
  const [activeTab, setActiveTab] = useState(0);
  const baseId = useId();

  if (!responses || responses.length === 0) {
    return null;
  }

  const selectTabFromKey = (event, index) => {
    const lastIndex = responses.length - 1;
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
    <div className="stage stage1">
      <h3 className="stage-title">Stage 1: Individual Responses</h3>

      <div className="tabs" role="tablist" aria-label="Stage 1 individual responses">
        {responses.map((resp, index) => (
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
            {resp.model.split('/')[1] || resp.model}
          </button>
        ))}
      </div>

      {responses.map((response, index) => (
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
              <div className="model-name">{response.model}</div>
              <div className="response-text markdown-content">
                <ReactMarkdown>{response.response}</ReactMarkdown>
              </div>
            </>
          )}
        </div>
      ))}
    </div>
  );
}
