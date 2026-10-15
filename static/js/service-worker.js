const CACHE_NAME = 'kpi-portal-v2';
const CACHE_PREFIX = 'kpi-portal-';
const ASSETS_TO_CACHE = [
  '/static/css/dashboard.css',
  '/static/css/input.css',
  '/static/manifest.json',
  // အခြား offline မှာ အလုပ်လုပ်စေချင်တဲ့ static ဖိုင်တွေကို ဒီမှာ ထပ်ထည့်နိုင်ပါတယ်
];

// Install Event - အက်ပ်စတင်ချိန်တွင် လိုအပ်သောဖိုင်များကို သိမ်းဆည်းရန်
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(async (cache) => {
        const results = await Promise.allSettled(
          ASSETS_TO_CACHE.map((asset) => cache.add(asset)),
        );
        results.forEach((result, index) => {
          if (result.status === 'rejected') {
            console.warn(`Service worker could not cache ${ASSETS_TO_CACHE[index]}.`);
          }
        });
      })
  );
});

// Activate Event - Cache အဟောင်းများကို ရှင်းလင်းရန်
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName.startsWith(CACHE_PREFIX) && cacheName !== CACHE_NAME) {
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
});

// Fetch Event - အင်တာနက်မရှိချိန်တွင် သိမ်းဆည်းထားသော Cache မှ ပြန်ယူရန်
self.addEventListener('fetch', (event) => {
  const request = event.request;
  const requestUrl = new URL(request.url);
  if (
    request.method !== 'GET'
    || request.mode === 'navigate'
    || requestUrl.origin !== self.location.origin
    || !requestUrl.pathname.startsWith('/static/')
  ) {
    return;
  }

  event.respondWith(
    caches.match(request)
      .then((response) => {
        // Cache တွင်ရှိပါက ၎င်းကိုပြန်ပေးမည်၊ မရှိပါက Network မှတဆင့် ဆက်လက်ခေါ်ယူမည်
        return response || fetch(request);
      })
  );
});