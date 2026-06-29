import { useEffect, useId, useRef, useState } from 'react';
import { ZONE_GRADIENT, ZONE_COLOR, usageToZone } from '@/lib/zones';

interface GaugeProps {
  /** Нагрузка 0..100, либо null (метрики недоступны → плейсхолдер «—»). */
  value: number | null;
  label: 'CPU' | 'RAM' | 'SSD';
  /** Максимальная ширина в px; по факту gauge заполняет контейнер (масштабируется). */
  size?: number;
}

// Геометрия: дуга 270°, разрыв 90° снизу (08-design-system.md).
const CX = 90;
const CY = 90;
const R = 70;
const STROKE = 13;
const SWEEP_DEG = 270;
const START_DEG = 225; // низ-слева
const ARC_LENGTH = (SWEEP_DEG / 360) * 2 * Math.PI * R;
const VIEW_W = 180;
const VIEW_H = 158;

/** angleDeg: 0 = верх, по часовой стрелке. */
function polar(angleDeg: number): { x: number; y: number } {
  const a = ((angleDeg - 90) * Math.PI) / 180;
  return { x: CX + R * Math.cos(a), y: CY + R * Math.sin(a) };
}

// Полный путь дуги (трек) — от START_DEG по часовой на 270°.
const START_PT = polar(START_DEG);
const END_PT = polar(START_DEG + SWEEP_DEG);
const ARC_PATH = `M ${START_PT.x.toFixed(3)} ${START_PT.y.toFixed(3)} A ${R} ${R} 0 1 1 ${END_PT.x.toFixed(3)} ${END_PT.y.toFixed(3)}`;

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () =>
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  );
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const handler = () => setReduced(mq.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);
  return reduced;
}

export function Gauge({ value, label, size = 188 }: GaugeProps) {
  const isPlaceholder = value == null;
  const clamped = isPlaceholder ? 0 : Math.max(0, Math.min(100, value));
  const zone = usageToZone(clamped);
  const gradient = ZONE_GRADIENT[zone];
  const glow = ZONE_COLOR[zone];
  const gradientId = useId();
  const glowId = useId();
  const reducedMotion = usePrefersReducedMotion();

  // Анимация от 0 при появлении и плавный transition при изменении.
  const [rendered, setRendered] = useState(reducedMotion ? clamped : 0);
  const mounted = useRef(false);
  useEffect(() => {
    if (reducedMotion) {
      setRendered(clamped);
      return;
    }
    if (!mounted.current) {
      mounted.current = true;
      const raf = requestAnimationFrame(() => setRendered(clamped));
      return () => cancelAnimationFrame(raf);
    }
    setRendered(clamped);
  }, [clamped, reducedMotion]);

  const dashOffset = ARC_LENGTH * (1 - rendered / 100);
  const rounded = Math.round(clamped);

  return (
    <div className="w-full" style={{ maxWidth: size }}>
      <svg
        className="block h-auto w-full"
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        role={isPlaceholder ? 'img' : 'meter'}
        aria-valuenow={isPlaceholder ? undefined : rounded}
        aria-valuemin={isPlaceholder ? undefined : 0}
        aria-valuemax={isPlaceholder ? undefined : 100}
        aria-label={
          isPlaceholder ? `Загрузка ${label} недоступна` : `Загрузка ${label} ${rounded} процентов`
        }
      >
        <defs>
          <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor={gradient.from} />
            <stop offset="100%" stopColor={gradient.to} />
          </linearGradient>
          <filter id={glowId} x="-30%" y="-30%" width="160%" height="160%">
            <feDropShadow dx="0" dy="0" stdDeviation="3" floodColor={glow} floodOpacity="0.7" />
          </filter>
        </defs>

        {/* Трек */}
        <path
          d={ARC_PATH}
          fill="none"
          stroke="var(--gauge-track)"
          strokeWidth={STROKE}
          strokeLinecap="round"
        />
        {/* Свечение (копия прогресс-дуги) */}
        <path
          d={ARC_PATH}
          fill="none"
          stroke={`url(#${gradientId})`}
          strokeWidth={STROKE}
          strokeLinecap="round"
          strokeDasharray={ARC_LENGTH}
          strokeDashoffset={dashOffset}
          filter={`url(#${glowId})`}
          style={reducedMotion ? undefined : { transition: 'stroke-dashoffset 450ms ease-out' }}
        />
        {/* Прогресс */}
        <path
          d={ARC_PATH}
          fill="none"
          stroke={`url(#${gradientId})`}
          strokeWidth={STROKE}
          strokeLinecap="round"
          strokeDasharray={ARC_LENGTH}
          strokeDashoffset={dashOffset}
          style={reducedMotion ? undefined : { transition: 'stroke-dashoffset 450ms ease-out' }}
        />

        {/* Центр: только моночисло (без % и без подписи «Usage») */}
        <text
          x={CX}
          y={CY + 2}
          textAnchor="middle"
          dominantBaseline="central"
          className="font-mono"
          fontSize={52}
          fontWeight={700}
          fill={isPlaceholder ? 'var(--text-tertiary)' : 'var(--text-primary)'}
        >
          {isPlaceholder ? '—' : rounded}
        </text>
      </svg>
    </div>
  );
}
