import React, { useRef } from 'react';

// Overlay, das NUR schließt, wenn Maus-Down UND Maus-Up auf dem Overlay selbst
// passieren. Fix: Beim Markieren von Text in Inputs (Drag nach außen) schloss
// sich das Popup vorher ungewollt.
export default function SafeOverlay({ className, onClose, children, testId }) {
  const downOnOverlay = useRef(false);
  return (
    <div
      className={className}
      data-testid={testId}
      onMouseDown={(e) => { downOnOverlay.current = e.target === e.currentTarget; }}
      onMouseUp={(e) => {
        if (downOnOverlay.current && e.target === e.currentTarget) onClose?.();
        downOnOverlay.current = false;
      }}
    >
      {children}
    </div>
  );
}
