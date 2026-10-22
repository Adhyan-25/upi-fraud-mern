/**
 * useDebounce - returns `value`, but only after it has stopped changing for `delay` ms.
 *
 * Used for the search box (don't hit the API on every keystroke) and could be used for any
 * rapidly changing input.
 */
import { useEffect, useState } from 'react';

export default function useDebounce(value, delay = 350) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id); // restart the timer if `value` changes again before it fires
  }, [value, delay]);
  return debounced;
}
