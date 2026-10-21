// slack-drafts lists and deletes your own Slack drafts. Slack's API has no way
// to edit a draft and the Slack MCP connector cannot delete one, so a revised
// draft otherwise lands next to the old one. This uses a slackdump user
// session (https://github.com/rusq/slackdump) and the web client's drafts.list
// and drafts.delete methods. The token is never printed.
//
// The workspace is the slackdump session name: -w <name>, else
// $SLACK_DRAFTS_WORKSPACE, else slackdump's current workspace.
//
//	slack-drafts [-w workspace] list [channel_id]
//	slack-drafts delete <draft_id>
//	slack-drafts clear <channel_id> [thread_ts]   (delete every active draft for that destination)
package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/rusq/encio"
	"github.com/rusq/slackdump/v4/auth"
)

const api = "https://slack.com/api/"

type draft struct {
	ID            string `json:"id"`
	LastUpdatedTS string `json:"last_updated_ts"`
	DateCreated   int64  `json:"date_created"`
	IsSent        bool   `json:"is_sent"`
	IsDeleted     bool   `json:"is_deleted"`
	Destinations  []struct {
		ChannelID string `json:"channel_id"`
		ThreadTS  string `json:"thread_ts"`
	} `json:"destinations"`
	Blocks json.RawMessage `json:"blocks"`
}

func call(c *http.Client, token, method string, v url.Values, out any) error {
	v.Set("token", token)
	resp, err := c.PostForm(api+method, v)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	return json.NewDecoder(resp.Body).Decode(out)
}

func text(b json.RawMessage) string {
	var parts []string
	var walk func(any)
	walk = func(x any) {
		switch t := x.(type) {
		case map[string]any:
			if s, ok := t["text"].(string); ok && t["type"] == "text" {
				parts = append(parts, s)
			}
			for _, v := range t {
				walk(v)
			}
		case []any:
			for _, v := range t {
				walk(v)
			}
		}
	}
	var x any
	_ = json.Unmarshal(b, &x)
	walk(x)
	s := strings.Join(parts, "")
	s = strings.ReplaceAll(s, "\n", " ")
	if len(s) > 90 {
		s = s[:90]
	}
	return s
}

func main() {
	args := os.Args[1:]
	ws := os.Getenv("SLACK_DRAFTS_WORKSPACE")
	if len(args) >= 2 && args[0] == "-w" {
		ws, args = args[1], args[2:]
	}
	os.Args = append([]string{os.Args[0]}, args...)
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: slack-drafts [-w workspace] list [channel_id] | delete <draft_id> | clear <channel_id> [thread_ts] | raw <draft_id>")
		os.Exit(2)
	}
	cache, _ := os.UserCacheDir()
	dir := filepath.Join(cache, "slackdump")
	if ws == "" {
		b, _ := os.ReadFile(filepath.Join(dir, "workspace.txt"))
		ws = strings.TrimSpace(string(b))
	}
	if ws == "" {
		fmt.Fprintln(os.Stderr, "no workspace: pass -w <name> or run `slackdump workspace new <name>`")
		os.Exit(2)
	}
	f, err := encio.Open(filepath.Join(dir, ws+".bin"))
	if err != nil {
		fmt.Fprintln(os.Stderr, "open session", ws+":", err, "(run `slackdump workspace new "+ws+"`)")
		os.Exit(1)
	}
	prov, err := auth.Load(f)
	f.Close()
	if err != nil {
		fmt.Fprintln(os.Stderr, "load session:", err)
		os.Exit(1)
	}
	c, err := prov.HTTPClient()
	if err != nil || c == nil {
		c = http.DefaultClient
	}
	token := prov.SlackToken()

	var list struct {
		OK     bool    `json:"ok"`
		Error  string  `json:"error"`
		Drafts []draft `json:"drafts"`
	}
	if err := call(c, token, "drafts.list", url.Values{"is_active": {"true"}, "limit": {"200"}}, &list); err != nil || !list.OK {
		fmt.Fprintln(os.Stderr, "drafts.list failed:", err, list.Error)
		os.Exit(1)
	}
	switch os.Args[1] {
	case "list":
		for _, d := range list.Drafts {
			if d.IsSent || d.IsDeleted {
				continue
			}
			ch, th := "", ""
			if len(d.Destinations) > 0 {
				ch, th = d.Destinations[0].ChannelID, d.Destinations[0].ThreadTS
			}
			if len(os.Args) > 2 && ch != os.Args[2] {
				continue
			}
			fmt.Printf("%s\t%s\tthread=%s\tcreated=%d\t%s\n", d.ID, ch, th, d.DateCreated, text(d.Blocks))
		}
	case "raw":
		var rawList struct {
			Drafts []map[string]any `json:"drafts"`
		}
		_ = call(c, token, "drafts.list", url.Values{"is_active": {"true"}, "limit": {"200"}}, &rawList)
		for _, d := range rawList.Drafts {
			if d["id"] == os.Args[2] {
				delete(d, "blocks")
				b, _ := json.Marshal(d)
				fmt.Println(string(b))
			}
		}
	case "clear":
		if len(os.Args) < 3 {
			fmt.Fprintln(os.Stderr, "clear needs a channel_id")
			os.Exit(2)
		}
		want := ""
		if len(os.Args) > 3 {
			want = os.Args[3]
		}
		n := 0
		for _, d := range list.Drafts {
			if d.IsSent || d.IsDeleted || len(d.Destinations) == 0 {
				continue
			}
			if d.Destinations[0].ChannelID != os.Args[2] || d.Destinations[0].ThreadTS != want {
				continue
			}
			var r struct {
				OK    bool   `json:"ok"`
				Error string `json:"error"`
			}
			ts := fmt.Sprintf("%.6f", float64(time.Now().UnixMicro())/1e6)
			if err := call(c, token, "drafts.delete", url.Values{"draft_id": {d.ID}, "client_last_updated_ts": {ts}}, &r); err != nil || !r.OK {
				fmt.Fprintln(os.Stderr, "drafts.delete failed:", d.ID, err, r.Error)
				os.Exit(1)
			}
			fmt.Println("deleted", d.ID, text(d.Blocks))
			n++
		}
		fmt.Println("cleared", n)
	case "delete":
		if len(os.Args) < 3 {
			fmt.Fprintln(os.Stderr, "delete needs a draft_id")
			os.Exit(2)
		}
		for _, d := range list.Drafts {
			if d.ID != os.Args[2] {
				continue
			}
			var r struct {
				OK    bool   `json:"ok"`
				Error string `json:"error"`
			}
			if err := call(c, token, "drafts.delete", url.Values{"draft_id": {d.ID}, "client_last_updated_ts": {fmt.Sprintf("%.6f", float64(time.Now().UnixMicro())/1e6)}}, &r); err != nil || !r.OK {
				fmt.Fprintln(os.Stderr, "drafts.delete failed:", err, r.Error)
				os.Exit(1)
			}
			fmt.Println("deleted", d.ID)
			return
		}
		fmt.Fprintln(os.Stderr, "no active draft", os.Args[2])
		os.Exit(1)
	}
}
