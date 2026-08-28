const CACHE_NAME = 'kpi-portal-v1';
const ASSETS_TO_CACHE = [
  '/auth/login',
  '/static/css/dashboard.css',
  '/static/css/input.css',
  '/static/manifest.json',
  // အခြား offline မှာ အလုပ်လုပ်စေချင်တဲ့ static ဖိုင်တွေကို ဒီမှာ ထပ်ထည့်နိုင်ပါတယ်
];

// Install Event - အက်ပ်စတင်ချိန်တွင် လိုအပ်သောဖိုင်များကို သိမ်းဆည်းရန်
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => {
        return cache.addAll(ASSETS_TO_CACHE);
      })
  );
});

// Activate Event - Cache အဟောင်းများကို ရှင်းလင်းရန်
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== CACHE_NAME) {
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
});

// Fetch Event - အင်တာနက်မရှိချိန်တွင် သိမ်းဆည်းထားသော Cache မှ ပြန်ယူရန်
self.addEventListener('fetch', (event) => {
  event.respondWith(
    caches.match(event.request)
      .then((response) => {
        // Cache တွင်ရှိပါက ၎င်းကိုပြန်ပေးမည်၊ မရှိပါက Network မှတဆင့် ဆက်လက်ခေါ်ယူမည်
        return response || fetch(event.request);
      })
  );
});