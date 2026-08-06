/**
 * Icon set — a single place where stroke weight and default size live.
 *
 * Everything renders in `currentColor`, so colour is set by CSS on the parent
 * and icons inherit it. Never hardcode a colour on an icon.
 */
import {
  AlertCircle,
  ArrowUpRight,
  Check,
  CheckSquare,
  ChevronDown,
  Download,
  FileAudio,
  Loader2,
  Mic,
  MicOff,
  Pause,
  Play,
  Plus,
  Search,
  Settings2,
  Sparkle,
  Square,
  Star,
  Trash2,
  Upload,
  Volume2,
  WifiOff,
  X,
} from 'lucide-react';

export interface IconProps {
  size?: number;
  className?: string;
  /** Decorative by default; set when the icon is the only label. */
  'aria-hidden'?: boolean;
}

const STROKE = 1.75;
const SIZE = 16;

function make(Cmp: typeof Star) {
  return function Icon({ size = SIZE, className }: IconProps) {
    return <Cmp size={size} strokeWidth={STROKE} className={className} aria-hidden="true" />;
  };
}

export const IconStar = make(Star);
export const IconTrash = make(Trash2);
export const IconDownload = make(Download);
export const IconPlay = make(Play);
export const IconPause = make(Pause);
export const IconMic = make(Mic);
export const IconMicOff = make(MicOff);
export const IconStop = make(Square);
export const IconUpload = make(Upload);
export const IconSettings = make(Settings2);
export const IconSearch = make(Search);
export const IconPlus = make(Plus);
export const IconCheck = make(Check);
export const IconCheckSquare = make(CheckSquare);
export const IconX = make(X);
export const IconAlert = make(AlertCircle);
export const IconChevronDown = make(ChevronDown);
export const IconFileAudio = make(FileAudio);
export const IconVolume = make(Volume2);
export const IconOffline = make(WifiOff);
export const IconRoute = make(ArrowUpRight);
export const IconSpark = make(Sparkle);

/** Spinner — the one icon that animates. */
export function IconSpinner({ size = SIZE, className }: IconProps) {
  return (
    <Loader2
      size={size}
      strokeWidth={STROKE}
      className={`spin ${className ?? ''}`}
      aria-hidden="true"
    />
  );
}
