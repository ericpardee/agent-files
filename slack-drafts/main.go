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
//	slack-drafts clear [--all] <channel_id> [thread_ts]   (delete the drafts the API created for that destination)
//	slack-drafts restore <backup.json>
//
// clear deletes only drafts the API created that no Slack client has touched
// since (empty last_updated_client). Drafts typed or edited in Slack are kept
// and listed, so hand-written notes and hand edits survive; --all deletes them
// too, and delete <draft_id> removes one after saving it.
//
// Before deleting, delete and clear save each draft to
// $SLACK_DRAFTS_BACKUP_DIR (default /tmp/slack-drafts) as <name>.txt (the
// message as markdown) and <name>.json (with the raw blocks), so edits made
// in the Slack composer survive a rewrite. A draft that cannot be saved is
// not deleted.
package main

import (
	"crypto/rand"
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
	FromComposer  bool   `json:"is_from_composer"`
	UpdatedClient string `json:"last_updated_client"`
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

// render turns a draft's rich_text blocks back into the markdown the draft
// tool accepts, keeping line breaks, list items, quotes and inline styles.
func render(b json.RawMessage) string {
	var blocks []map[string]any
	_ = json.Unmarshal(b, &blocks)
	inline := func(els []any) string {
		var sb strings.Builder
		for _, e := range els {
			m, _ := e.(map[string]any)
			s, _ := m["text"].(string)
			switch m["type"] {
			case "link":
				u, _ := m["url"].(string)
				if s == "" {
					s = u
				}
				s = "[" + s + "](" + u + ")"
			case "user":
				s = "<@" + fmt.Sprint(m["user_id"]) + ">"
			case "channel":
				s = "<#" + fmt.Sprint(m["channel_id"]) + ">"
			case "emoji":
				s = ":" + fmt.Sprint(m["name"]) + ":"
			case "broadcast":
				s = "@" + fmt.Sprint(m["range"])
			}
			if st, ok := m["style"].(map[string]any); ok {
				for _, k := range []struct{ key, mark string }{{"code", "`"}, {"strike", "~"}, {"italic", "_"}, {"bold", "**"}} {
					if st[k.key] == true {
						s = k.mark + s + k.mark
					}
				}
			}
			sb.WriteString(s)
		}
		return sb.String()
	}
	var out []string
	for _, blk := range blocks {
		els, _ := blk["elements"].([]any)
		for _, e := range els {
			m, _ := e.(map[string]any)
			sub, _ := m["elements"].([]any)
			switch m["type"] {
			case "rich_text_section":
				out = append(out, inline(sub))
			case "rich_text_list":
				indent, _ := m["indent"].(float64)
				for i, item := range sub {
					im, _ := item.(map[string]any)
					ie, _ := im["elements"].([]any)
					mark := "- "
					if m["style"] == "ordered" {
						mark = fmt.Sprintf("%d. ", i+1)
					}
					out = append(out, strings.Repeat("  ", int(indent))+mark+inline(ie)+"\n")
				}
			case "rich_text_quote":
				out = append(out, "> "+strings.ReplaceAll(inline(sub), "\n", "\n> ")+"\n")
			case "rich_text_preformatted":
				out = append(out, "```\n"+inline(sub)+"\n```\n")
			}
		}
	}
	return strings.Join(out, "")
}

// backup writes the draft to the backup directory before it is deleted and
// returns the path of the .txt copy.
func backup(d draft) (string, error) {
	dir := os.Getenv("SLACK_DRAFTS_BACKUP_DIR")
	if dir == "" {
		dir = "/tmp/slack-drafts"
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", err
	}
	ch, th := "", ""
	if len(d.Destinations) > 0 {
		ch, th = d.Destinations[0].ChannelID, d.Destinations[0].ThreadTS
	}
	name := ch
	if th != "" {
		name += "-" + th
	}
	name += "-" + d.ID + "-" + time.Now().Format("20060102-150405")
	body := render(d.Blocks)
	meta, _ := json.MarshalIndent(map[string]any{
		"draft_id": d.ID, "channel_id": ch, "thread_ts": th,
		"last_updated_ts": d.LastUpdatedTS, "saved_at": time.Now().Format(time.RFC3339),
		"text": body, "blocks": d.Blocks,
	}, "", "  ")
	if err := os.WriteFile(filepath.Join(dir, name+".json"), meta, 0o600); err != nil {
		return "", err
	}
	txt := filepath.Join(dir, name+".txt")
	return txt, os.WriteFile(txt, []byte(body), 0o600)
}

func uuid() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	b[6] = b[6]&0x0f | 0x40
	b[8] = b[8]&0x3f | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:])
}

func main() {
	args := os.Args[1:]
	ws := os.Getenv("SLACK_DRAFTS_WORKSPACE")
	if len(args) >= 2 && args[0] == "-w" {
		ws, args = args[1], args[2:]
	}
	os.Args = append([]string{os.Args[0]}, args...)
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: slack-drafts [-w workspace] list [channel_id] | delete <draft_id> | clear [--all] <channel_id> [thread_ts] | restore <backup.json> | raw <draft_id>")
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
		all := false
		if len(os.Args) > 2 && os.Args[2] == "--all" {
			all = true
			os.Args = append(os.Args[:2], os.Args[3:]...)
		}
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
			if (d.FromComposer || d.UpdatedClient != "") && !all {
				fmt.Println("kept", d.ID, "(typed or edited in Slack; delete <draft_id> or --all)", text(d.Blocks))
				continue
			}
			saved, err := backup(d)
			if err != nil {
				fmt.Fprintln(os.Stderr, "not deleted, backup failed:", d.ID, err)
				os.Exit(1)
			}
			fmt.Println("saved", saved)
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
	case "restore":
		if len(os.Args) < 3 {
			fmt.Fprintln(os.Stderr, "restore needs a backup .json path")
			os.Exit(2)
		}
		b, err := os.ReadFile(os.Args[2])
		if err != nil {
			fmt.Fprintln(os.Stderr, "read backup:", err)
			os.Exit(1)
		}
		var bk struct {
			ChannelID string          `json:"channel_id"`
			ThreadTS  string          `json:"thread_ts"`
			Blocks    json.RawMessage `json:"blocks"`
		}
		if err := json.Unmarshal(b, &bk); err != nil || bk.ChannelID == "" || len(bk.Blocks) == 0 {
			fmt.Fprintln(os.Stderr, "not a slack-drafts backup:", err)
			os.Exit(1)
		}
		dest := map[string]string{"channel_id": bk.ChannelID}
		if bk.ThreadTS != "" {
			dest["thread_ts"] = bk.ThreadTS
		}
		dj, _ := json.Marshal([]map[string]string{dest})
		var r struct {
			OK    bool   `json:"ok"`
			Error string `json:"error"`
			Draft draft  `json:"draft"`
		}
		v := url.Values{"blocks": {string(bk.Blocks)}, "destinations": {string(dj)}, "file_ids": {"[]"},
			"is_from_composer": {"true"}, "client_msg_id": {uuid()}}
		if err := call(c, token, "drafts.create", v, &r); err != nil || !r.OK {
			fmt.Fprintln(os.Stderr, "drafts.create failed:", err, r.Error)
			os.Exit(1)
		}
		fmt.Println("restored", r.Draft.ID, text(r.Draft.Blocks))
	case "delete":
		if len(os.Args) < 3 {
			fmt.Fprintln(os.Stderr, "delete needs a draft_id")
			os.Exit(2)
		}
		for _, d := range list.Drafts {
			if d.ID != os.Args[2] {
				continue
			}
			saved, err := backup(d)
			if err != nil {
				fmt.Fprintln(os.Stderr, "not deleted, backup failed:", d.ID, err)
				os.Exit(1)
			}
			fmt.Println("saved", saved)
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
