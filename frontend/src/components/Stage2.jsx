import { useId, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import './Stage2.css';

function deAnonymizeText(text, labelToModel) {
  if (!labelToModel) return text;

  let result = text;
  // Replace each "Response X" with the actual model name
  Object.entries(labelToModel).forEach(([label, model]) => {
    const modelShortName = model.split('/')[1] || model;
    result = result.replace(new RegExp(label, 'g'), `**${modelShortName}**`);
  });
  return result;
}

export default function Stage2({ rankings, labelToModel, aggregateRankings }) {
  const [activeTab, setActiveTab] = useState(0);
  const baseId = useId();

  if (!rankings || rankings.length === 0) {
    return null;
  }

  const selectTabFromKey = (event, index) => {
    const lastIndex = rankings.length - 1;
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
    <div className="stage stage2">
      <h3 className="stage-title">Stage 2: Peer Rankings</h3>

      <h4>Raw Evaluations</h4>
      <p className="stage-description">
        Each model evaluated all responses (anonymized as Response A, B, C, etc.) and provided rankings.
        Below, model names are shown in <strong>bold</strong> for readability, but the original evaluation used anonymous labels.
      </p>

      <div className="tabs" role="tablist" aria-label="Stage 2 peer rankings">
        {rankings.map((rank, index) => (
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
            {rank.model.split('/')[1] || rank.model}
          </button>
        ))}
      </div>

      {rankings.map((ranking, index) => (
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
              <div className="ranking-model">
                {ranking.model}
              </div>
              <div className="ranking-content markdown-content">
                <ReactMarkdown>
                  {deAnonymizeText(ranking.ranking, labelToModel)}
                </ReactMarkdown>
              </div>

              {ranking.parsed_ranking && ranking.parsed_ranking.length > 0 && (
                <div className="parsed-ranking">
                  <strong>Extracted Ranking:</strong>
                  <ol>
                    {ranking.parsed_ranking.map((label, i) => (
                      <li key={i}>
                        {labelToModel && labelToModel[label]
                          ? labelToModel[label].split('/')[1] || labelToModel[label]
                          : label}
                      </li>
                    ))}
                  </ol>
                </div>
              )}
            </>
          )}
        </div>
      ))}

      {aggregateRankings && aggregateRankings.length > 0 && (
        <div className="aggregate-rankings">
          <h4>Aggregate Rankings (Street Cred)</h4>
          <p className="stage-description">
            Combined results across all peer evaluations (lower score is better):
          </p>
          <div className="aggregate-list">
            {aggregateRankings.map((agg, index) => (
              <div key={index} className="aggregate-item">
                <span className="rank-position">#{index + 1}</span>
                <span className="rank-model">
                  {agg.model.split('/')[1] || agg.model}
                </span>
                <span className="rank-score">
                  Avg: {agg.average_rank.toFixed(2)}
                </span>
                <span className="rank-count">
                  ({agg.rankings_count} votes)
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
