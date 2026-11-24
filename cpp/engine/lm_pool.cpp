#include "lm_pool.h"

#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <exception>
#include <mutex>
#include <thread>
#include <vector>

namespace sz::lmq {

namespace {

std::atomic<int> g_threads{1};
thread_local bool t_in_job = false;  // (a task asking for more threads runs them itself)

using Fn = std::function<void(size_t, int)>;

struct Pool {
    std::mutex busy;  // one job at a time
    std::mutex m;     // the fields below
    std::condition_variable wake, done;
    const Fn* fn = nullptr;
    size_t count = 0;
    int slots = 1;  // (threads in this job: worker w takes part when w < slots)
    std::atomic<size_t> next{0};
    size_t running = 0;          // workers not yet through this job (all of them, taking part or not)
    unsigned long long job = 0;  // (never wraps: a new worker starts at 0 and must see the job it is made for)
    std::exception_ptr error;
    std::vector<std::thread> workers;

    void take(const Fn& f, size_t n, int slot) {
        for (size_t i; (i = next.fetch_add(1)) < n;) {
            try {
                f(i, slot);
            } catch (...) {
                std::lock_guard<std::mutex> lk(m);
                if (!error) error = std::current_exception();
                next = n;  // (no more tasks)
            }
        }
    }

    void work(int w) {
        t_in_job = true;
        unsigned long long seen = 0;
        for (;;) {
            const Fn* f = nullptr;
            size_t n = 0;
            bool part = false;
            {
                std::unique_lock<std::mutex> lk(m);
                wake.wait(lk, [&] { return job != seen; });
                seen = job;
                f = fn;
                n = count;
                part = w < slots;
            }
            if (part) take(*f, n, w);
            std::lock_guard<std::mutex> lk(m);
            if (--running == 0) done.notify_one();
        }
    }
};

Pool* pool() {
    static Pool* p = new Pool;  // (never deleted: its threads wait for work until the process ends)
    return p;
}

}  // namespace

void set_threads(int n) { g_threads = std::max(1, n); }

int threads() { return g_threads; }

int default_threads() {
    const int logical = static_cast<int>(std::thread::hardware_concurrency());
    return std::max(1, std::min(4, logical / 2));
}

void parallel_for(size_t count, int slots, const Fn& fn) {
    const int want = std::min(slots, static_cast<int>(g_threads));
    if (count <= 1 || want <= 1 || t_in_job) {
        for (size_t i = 0; i < count; ++i) fn(i, 0);
        return;
    }
    Pool* p = pool();
    std::unique_lock<std::mutex> one(p->busy, std::try_to_lock);
    if (!one.owns_lock()) {  // (another thread's job is running)
        for (size_t i = 0; i < count; ++i) fn(i, 0);
        return;
    }
    {
        std::lock_guard<std::mutex> lk(p->m);
        while (p->workers.size() + 1 < static_cast<size_t>(want)) {
            const int w = static_cast<int>(p->workers.size()) + 1;
            try {
                p->workers.emplace_back([p, w] { p->work(w); });
            } catch (...) {  // (no thread to be had: fewer of them, the same results)
                break;
            }
        }
        p->fn = &fn;
        p->count = count;
        p->slots = std::min(want, static_cast<int>(p->workers.size()) + 1);
        p->next = 0;
        p->error = nullptr;
        p->running = p->workers.size();
        ++p->job;
    }
    p->wake.notify_all();
    t_in_job = true;
    p->take(fn, count, 0);
    t_in_job = false;
    std::exception_ptr error;
    {
        std::unique_lock<std::mutex> lk(p->m);
        p->done.wait(lk, [&] { return p->running == 0; });
        error = p->error;
        p->fn = nullptr;
    }
    if (error) std::rethrow_exception(error);
}

}  // namespace sz::lmq
