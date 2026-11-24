// The threads the language models may use for one large batch (#59): the rows of a matrix, or groups of states, handed
// out to a few threads. Every task writes its own outputs with the same integer arithmetic, so the results are the same
// whatever the number of threads.
#pragma once

#include <cstddef>
#include <functional>

namespace sz::lmq {

// 1, the default, runs every task on the calling thread and starts no thread: the input method's DLL, loaded into
// every app, never raises it. The engine program and sz_trace set default_threads() at start.
void set_threads(int n);
int threads();
// min(4, half the logical processors), at least 1 (the user, 2026-11-24)
int default_threads();

// below this many multiply-adds a batch stays on one thread (waking the others costs more than they save)
constexpr size_t PARALLEL_MACS = size_t(1) << 22;

// fn(task, slot) for task 0 .. count - 1, each once, on up to min(slots, threads()) threads including the calling one;
// returns when all are done. slot (0 .. slots - 1) is the thread's own: two tasks running at once never share one, so
// a caller may give each slot its own buffer. Tasks are taken one at a time, so a slower core takes fewer. A call while
// another is running (another thread, or from inside a task) runs all its tasks on its own thread as slot 0. An
// exception from a task is thrown here after the running ones end.
void parallel_for(size_t count, int slots, const std::function<void(size_t, int)>& fn);

}  // namespace sz::lmq
