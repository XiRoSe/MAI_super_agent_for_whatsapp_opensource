export function Brand({ size = 22, withText = true }: { size?: number; withText?: boolean }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <img src="/mai.svg" width={size} height={size} alt="Mai" style={{ filter: "drop-shadow(0 0 10px rgba(94,255,176,0.45))" }} />
      {withText && (
        <span className="mai-wordmark" style={{ fontSize: size * 0.92 }}>
          Mai
        </span>
      )}
    </div>
  );
}
