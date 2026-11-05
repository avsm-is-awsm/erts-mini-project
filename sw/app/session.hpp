// session.hpp -- SessionController (State pattern, SPEC 10.2) + Observer interface.
//
//   Idle --load--> Loaded --configure--> Configured --run--> Running --> Draining --> Done
//     ^                                                         |                      |
//     +--------------------------- reset ---------------------- Error <-----(failure)--+
//
// Matches the SysML state machine D09_stm_session.  Observers (ResultWriter, CLI progress)
// are notified of every transition and of every finished run.
#ifndef DK1_SESSION_HPP
#define DK1_SESSION_HPP

#include <string.h>

#include "dataset.hpp"
#include "engines.hpp"

namespace app {

enum SessionState { S_IDLE, S_LOADED, S_CONFIGURED, S_RUNNING, S_DRAINING, S_DONE, S_ERROR };

inline const char* state_name(SessionState s) {
    static const char* n[] = {"Idle", "Loaded", "Configured", "Running", "Draining", "Done", "Error"};
    return n[s];
}

class ISessionObserver {
public:
    virtual ~ISessionObserver() {}
    virtual void on_state(SessionState from, SessionState to, const char* why) = 0;
    virtual void on_result(const Dataset& ds, const RunResult& r) = 0;
};

class SessionController {
public:
    enum { MAX_OBS = 4 };

    explicit SessionController(DatasetLoader& loader) : loader_(loader), state_(S_IDLE), n_obs_(0) {
        memset(&ds_, 0, sizeof(ds_));
        cfg_ = t2t::default_config();
    }

    void attach(ISessionObserver* o) {
        if (n_obs_ < MAX_OBS) obs_[n_obs_++] = o;
    }

    SessionState state() const { return state_; }
    const Dataset& dataset() const { return ds_; }
    t2t::Config& config() { return cfg_; }

    bool load(const char* dir) {
        if (state_ == S_RUNNING || state_ == S_DRAINING) return false;
        const char* err = loader_.load(dir, ds_);
        if (*err) return go(S_ERROR, err);
        cfg_ = ds_.cfg;  // dataset config.txt is the default (golden runs use the same file)
        return go(S_LOADED, "dataset loaded");
    }

    bool configure() {
        if (state_ != S_LOADED && state_ != S_CONFIGURED && state_ != S_DONE) return false;
        return go(S_CONFIGURED, "configuration accepted");
    }

    // Run one engine; Running -> Draining -> Done, or -> Error.
    bool run(IEngine& e, RunResult& r) {
        if (state_ == S_LOADED || state_ == S_DONE) configure();
        if (state_ != S_CONFIGURED) return false;
        go(S_RUNNING, e.name());
        const bool ok = e.run(ds_, cfg_, r);
        if (!ok) return go(S_ERROR, r.error[0] ? r.error : "engine failed");
        go(S_DRAINING, "collecting results");
        for (int i = 0; i < n_obs_; ++i) obs_[i]->on_result(ds_, r);
        return go(S_DONE, "run complete");
    }

    bool reset() { return go(ds_.loaded ? S_LOADED : S_IDLE, "reset"); }

private:
    bool go(SessionState to, const char* why) {
        const SessionState from = state_;
        state_ = to;
        for (int i = 0; i < n_obs_; ++i) obs_[i]->on_state(from, to, why);
        return to != S_ERROR;
    }

    DatasetLoader& loader_;
    Dataset ds_;
    t2t::Config cfg_;
    SessionState state_;
    ISessionObserver* obs_[MAX_OBS];
    int n_obs_;
};

}  // namespace app

#endif
