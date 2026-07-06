import { FormEvent, useEffect, useRef, useState } from "react";
import { Camera, X } from "lucide-react";
import { resolveMediaUrl, updateProfile, uploadAvatar } from "./api";
import type { UserProfile } from "./types";
import { getErrorMessage } from "./components";

export function EditProfileModal({
  profile,
  onClose,
  onSaved,
}: {
  profile: UserProfile;
  onClose: () => void;
  onSaved: (profile: UserProfile) => void;
}) {
  const [bio, setBio] = useState(profile.bio ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!file) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const avatarSrc = previewUrl ?? resolveMediaUrl(profile.avatar_url);
  const remaining = 160 - bio.length;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      let updated = profile;
      if (file) {
        updated = await uploadAvatar(file);
      }
      if (bio !== (profile.bio ?? "")) {
        updated = await updateProfile(bio);
      }
      onSaved(updated);
      onClose();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="edit-profile-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="modal-header">
          <button className="icon-button" onClick={onClose} aria-label="Close">
            <X size={20} aria-hidden="true" />
          </button>
          <h2 id="edit-profile-title">Edit profile</h2>
          <button
            className="primary-button compact"
            form="edit-profile-form"
            disabled={saving}
          >
            {saving ? "Saving..." : "Save"}
          </button>
        </header>
        <form id="edit-profile-form" onSubmit={handleSubmit} className="modal-body">
          <div className="edit-avatar">
            {avatarSrc ? (
              <img className="avatar large avatar-image" src={avatarSrc} alt="Avatar preview" />
            ) : (
              <div className="avatar large" aria-hidden="true">
                {profile.username.slice(0, 1).toUpperCase()}
              </div>
            )}
            <button
              type="button"
              className="icon-button edit-avatar-button"
              onClick={() => fileInputRef.current?.click()}
              aria-label="Choose profile picture"
            >
              <Camera size={20} aria-hidden="true" />
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              hidden
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </div>
          <label className="edit-bio">
            <span>Bio</span>
            <textarea
              value={bio}
              onChange={(event) => setBio(event.target.value)}
              maxLength={160}
              rows={3}
              placeholder="Describe yourself"
            />
            <span className={remaining < 20 ? "counter warn" : "counter"}>{remaining}</span>
          </label>
          {error ? <p className="form-error">{error}</p> : null}
        </form>
      </section>
    </div>
  );
}
