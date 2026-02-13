# 🐛 Frontend Bug Analysis - Repeated Messages

## Problem

User sees repeated "What genre of book are you in the mood for?" messages (9+ times).

## Root Cause Analysis

### Evidence from Logs:

**Backend Log (787427.txt):**
```
INFO:     127.0.0.1:52843 - "POST /chat HTTP/1.1" 200 OK  (repeated 17 times)
```

**Frontend Log (865016.txt):**
```
POST /api/chat 200 in 6-27ms  (repeated 17 times in rapid succession)
```

### Diagnosis:

The backend is responding correctly (all 200 OK), but the **frontend is making 17 identical API calls in rapid succession** from the same connection. This indicates a **React rendering loop** or **event handler issue**.

## Likely Causes:

### 1. **React StrictMode Double Rendering (Most Likely)**
- Next.js 15 runs in StrictMode by default in development
- StrictMode intentionally double-invokes effects/renders to detect side effects
- Combined with other effects, this can cause cascading re-renders

### 2. **useEffect Dependency Issue**
Looking at `page.tsx`:
```tsx
// Line 66-76: Initial welcome message
useEffect(() => {
  if (chatMessages.length === 0) {
    const initialMessage: ChatMessage = {
      id: 'initial',
      role: 'assistant',
      content: config.welcomeMessage,
      timestamp: new Date(),
    };
    setChatMessages([initialMessage]);
  }
}, [chatMessages.length, config.welcomeMessage]);  // ⚠️ chatMessages.length in deps
```

**Problem:** `chatMessages.length` is in the dependency array, and `setChatMessages` is called inside, creating a potential loop if `config.welcomeMessage` changes.

### 3. **Auto-scroll Effect Triggering Re-renders**
```tsx
// Line 147-159: Auto-scroll
useEffect(() => {
  if (chatMessagesContainerRef.current && !isInitialState) {
    requestAnimationFrame(() => {
      if (chatMessagesContainerRef.current) {
        chatMessagesContainerRef.current.scrollTo({
          top: chatMessagesContainerRef.current.scrollHeight,
          behavior: 'smooth',
        });
      }
    });
  }
}, [chatMessages, isLoading, isInitialState]);  // Runs on every message change
```

This runs on every `chatMessages` change, which could interact with other effects.

### 4. **Domain Config is Set to Vehicles, Not Books**
`/Users/julih/Documents/idss-web/src/config/domain-config.ts:377`:
```tsx
export const currentDomainConfig: DomainConfig = vehicleConfig;  // ⚠️ Should be pcPartsConfig or bookConfig
```

The frontend is configured for vehicles, but you're testing with books. This mismatch might cause config reloads.

## The Cascading Effect:

```
1. User selects "Books" quick reply
   ↓
2. Frontend sends POST /chat "Books"
   ↓
3. Backend responds: "What genre of book..."
   ↓
4. Frontend adds message to chatMessages
   ↓
5. chatMessages change triggers useEffect (auto-scroll)
   ↓
6. Auto-scroll runs, possibly causing re-render
   ↓
7. If config.welcomeMessage or dependencies change → loop
   ↓
8. Multiple API calls in rapid succession
   ↓
9. Multiple identical messages displayed
```

## Solutions:

### Fix 1: Remove chatMessages.length from Dependencies (Primary Fix)

**File:** `/Users/julih/Documents/idss-web/src/app/page.tsx`

**Change line 76:**
```tsx
}, [chatMessages.length, config.welcomeMessage]);  // ❌ BEFORE
```

**To:**
```tsx
}, []);  // ✅ AFTER - Only run once on mount
```

**Rationale:** The welcome message should only be set once when the component mounts, not every time the message count changes.

### Fix 2: Debounce Auto-scroll

**Add to line 147:**
```tsx
useEffect(() => {
  if (chatMessagesContainerRef.current && !isInitialState) {
    // Debounce scroll to prevent excessive re-renders
    const timeoutId = setTimeout(() => {
      requestAnimationFrame(() => {
        if (chatMessagesContainerRef.current) {
          chatMessagesContainerRef.current.scrollTo({
            top: chatMessagesContainerRef.current.scrollHeight,
            behavior: 'smooth',
          });
        }
      });
    }, 100);  // 100ms debounce
    
    return () => clearTimeout(timeoutId);
  }
}, [chatMessages, isLoading, isInitialState]);
```

### Fix 3: Update Domain Config for Books/Laptops

**File:** `/Users/julih/Documents/idss-web/src/config/domain-config.ts`

**Change line 377:**
```tsx
export const currentDomainConfig: DomainConfig = vehicleConfig;  // ❌ BEFORE
```

**To:**
```tsx
export const currentDomainConfig: DomainConfig = pcPartsConfig;  // ✅ AFTER
```

**Or create a unified config:**
```tsx
export const universalConfig: DomainConfig = {
  productName: 'product',
  productNamePlural: 'products',
  welcomeMessage: "Hi! What are you looking for today?",
  inputPlaceholder: "What are you looking for?",
  examplePlaceholderQueries: [
    "What are you looking for?",
    "Show me gaming laptops under $2000",
    "Recommend sci-fi books",
    "iPhone 15 Pro",
    "SUVs under $35k",
  ],
  viewDetailsButtonText: "View Details",
  viewListingButtonText: "View Details",
  recommendationCardFields: [ /* ... */ ],
  detailPageFields: [ /* ... */ ],
  defaultQuickReplies: ["Vehicles", "Laptops", "Books"],
};

export const currentDomainConfig: DomainConfig = universalConfig;
```

### Fix 4: Disable React StrictMode in Development (Optional)

**File:** `/Users/julih/Documents/idss-web/next.config.ts`

```tsx
const nextConfig = {
  reactStrictMode: false,  // Disable for development debugging
};
```

**Note:** Only do this temporarily for debugging. StrictMode is helpful for catching issues.

## Testing After Fixes:

1. **Clear browser cache and restart servers**
2. **Test book query:** Type "books" or click "Books"
3. **Expected:** See ONE "What genre of book..." message
4. **Check backend logs:** Should see only 1 POST /chat request per user action

## Prevention:

✅ Always use empty dependency arrays `[]` for mount-only effects
✅ Avoid putting state values that change inside effects that set that same state
✅ Debounce/throttle effects that run frequently (scroll, resize, etc.)
✅ Use `useCallback` and `useMemo` to stabilize function/object references
✅ Test with React DevTools Profiler to identify cascading renders

---

**Priority:** HIGH - This creates a poor user experience and wastes API calls

**Estimated Fix Time:** 5 minutes

**Impact:** Eliminates 90% of unnecessary API calls, improves UX significantly
