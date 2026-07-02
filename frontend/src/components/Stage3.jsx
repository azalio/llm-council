import ReactMarkdown from 'react-markdown';
import './Stage3.css';

const STAGE_LABELS = {
  stage1: 'Stage 1',
  stage2: 'Stage 2',
  stage2a: 'Stage 2a',
  stage2b: 'Stage 2b',
  stage3: 'Stage 3',
};

function formatFailedModels(failedModels) {
  return failedModels
    .map((item) => (
      item.model ? `${item.model} (${item.failure_type})` : item.failure_type
    ))
    .join(', ');
}

function formatModelName(model) {
  return model.split('/')[1] || model;
}

export default function Stage3({ finalResponse, runStatus, councilConfidence, labelToModel }) {
  if (!finalResponse) {
    return null;
  }

  const degradedStages = Object.entries(runStatus?.stages ?? {}).filter(
    ([, stage]) => stage.failed_models_count > 0
  );
  const attribution = finalResponse.attribution || runStatus?.chairman_attribution;
  const attributionEntries = Object.entries(labelToModel ?? {}).sort(([a], [b]) =>
    a.localeCompare(b)
  );

  return (
    <div className="stage stage3">
      <h3 className="stage-title">Stage 3: Final Council Answer</h3>
      {runStatus?.degraded && (
        <div className="run-status-banner" role="status">
          <div className="run-status-label">Degraded run</div>
          <div className="run-status-summary">{runStatus.summary}</div>
          {degradedStages.map(([stageName, stage]) => (
            <div key={stageName} className="run-status-detail">
              <span className="run-status-stage">
                {STAGE_LABELS[stageName] || stageName}
              </span>
              <span className="run-status-text">
                {stage.failed_models_count} failure
                {stage.failed_models_count === 1 ? '' : 's'}
                {stage.failed_models.length > 0
                  ? `: ${formatFailedModels(stage.failed_models)}`
                  : ''}
              </span>
            </div>
          ))}
        </div>
      )}
      {runStatus?.deliberation_mode === 'quick' && !runStatus?.degraded && (
        <div className="run-status-banner" role="status">
          <div className="run-status-label">Quick mode</div>
          <div className="run-status-summary">{runStatus.summary}</div>
        </div>
      )}
      {councilConfidence?.low_confidence && (
        <div className="council-confidence-banner" role="status">
          <div className="run-status-label">Low confidence</div>
          <div className="run-status-summary">{councilConfidence.summary}</div>
          <div className="run-status-detail">
            <span className="run-status-stage">Stage 2</span>
            <span className="run-status-text">
              Top-1 stability {councilConfidence.top1_stability}, rank agreement{' '}
              {councilConfidence.rank_agreement ?? 'unavailable'}, disagreement score{' '}
              {councilConfidence.disagreement_score ?? 'unavailable'}
            </span>
          </div>
        </div>
      )}
      {attribution?.unattributed_claim_count > 0 && (
        <div className="chairman-attribution-banner" role="status">
          <div className="run-status-label">Attribution warning</div>
          <div className="run-status-summary">{attribution.summary}</div>
          {attribution.unattributed_claims?.map((claim) => (
            <div key={claim} className="run-status-detail">
              <span className="run-status-text">{claim}</span>
            </div>
          ))}
        </div>
      )}
      <div className="final-response">
        <div className="chairman-label">
          Chairman: {formatModelName(finalResponse.model)}
        </div>
        {attributionEntries.length > 0 && (
          <div className="attribution-key" aria-label="Council attribution key">
            {attributionEntries.map(([label, model]) => {
              const marker = `[${label.replace('Response ', '')}]`;
              return (
                <span key={label} className="attribution-key-item" title={model}>
                  <span className="attribution-marker">{marker}</span> {formatModelName(model)}
                </span>
              );
            })}
          </div>
        )}
        <div className="final-text markdown-content">
          <ReactMarkdown>{finalResponse.response}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
